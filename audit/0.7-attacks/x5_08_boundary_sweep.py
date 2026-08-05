"""x5-08 -- abandon the stream at EVERY index and check `complete` each time.

The interesting boundary is the terminal: a consumer that receives
`run_completed` and then breaks leaves the generator suspended AFTER the
terminal was persisted but BEFORE `_managed_execute`'s `finally`.  If
`_saw_terminal` were read or set anywhere but where it is, this is where it
would show.

For every k in 0..N: consume k records, drop the driver, then assert
`finish(complete)` == "the file ends with a terminal".  Repeated for all
three durability profiles and for both drivers.
"""

from __future__ import annotations

import asyncio
import gc

from vitruvyan_motus import Runtime, State

from x5_common import (
    BUFFERED, INMEM, LINEAR, LINEAR_SPEC_PATH, OUT, SYNC, ProtocolJsonlSink,
    Report, registry, validate_file,
)

D = OUT / "x5_08"
N = 16  # the linear graph emits 14 records; sweep past the end


def sync_case(profile, k: int, tag: str) -> ProtocolJsonlSink:
    s = ProtocolJsonlSink(D, prefix=f"{tag}-{k:02d}")
    kw = {"durability_profile": profile}
    if profile is BUFFERED:
        kw |= {"chunk_records": 1000, "flush_interval_ms": 60000}
    rt = Runtime(LINEAR, registry(), sink=s, **kw)
    drv = rt.stream(State.empty(f"k{k}"))
    seen = 0
    for _ in drv:
        seen += 1
        if seen >= k:
            break
    del drv
    gc.collect()
    return s


def async_case(profile, k: int, tag: str) -> ProtocolJsonlSink:
    s = ProtocolJsonlSink(D, prefix=f"{tag}-{k:02d}")
    kw = {"durability_profile": profile}
    if profile is BUFFERED:
        kw |= {"chunk_records": 1000, "flush_interval_ms": 60000}

    async def go():
        rt = Runtime(LINEAR, registry(), sink=s, **kw)
        drv = rt.astream(State.empty(f"k{k}"))
        seen = 0
        async for _ in drv:
            seen += 1
            if seen >= k:
                break
        del drv
        gc.collect()

    asyncio.run(go())
    gc.collect()
    return s


def main() -> int:
    r = Report("x5-08 abandon at every index")
    bad: list[str] = []
    invalid: list[str] = []
    unfinished: list[str] = []

    for label, profile in (("sync", SYNC), ("buf", BUFFERED), ("mem", INMEM)):
        for driver_name, fn in (("stream", sync_case), ("astream", async_case)):
            for k in range(0, N + 1):
                tag = f"{label}-{driver_name}"
                s = fn(profile, k, tag)
                ses = s.sessions[0]
                if not ses.finish_calls:
                    unfinished.append(f"{tag}@k={k}")
                    continue
                claimed = ses.finish_calls[0]
                if claimed != ses.file_has_terminal:
                    bad.append(
                        f"{tag}@k={k}: complete={claimed} "
                        f"file_terminal={ses.file_has_terminal} kinds={ses.kinds}"
                    )
                if len(ses.kinds) > 1:  # zero-record files cannot validate
                    code, out = validate_file(
                        ses.path, spec_path=LINEAR_SPEC_PATH,
                        allow_incomplete=not claimed,
                    )
                    if code != 0:
                        invalid.append(
                            f"{tag}@k={k}: {out.splitlines()[0] if out else ''}"
                        )

    total = 3 * 2 * (N + 1)
    r.record(
        f"complete agrees with the file at every abandonment index ({total} cases)",
        not bad, f"{len(bad)} disagreements" + (f" :: {bad[:2]}" if bad else ""),
    )
    r.record(
        "every non-empty artifact validates in its declared mode",
        not invalid,
        f"{len(invalid)} rejected" + (f" :: {invalid[:2]}" if invalid else ""),
    )
    r.record(
        "every opened session receives finish()",
        not unfinished,
        f"{len(unfinished)} sessions never told anything: {unfinished[:6]}",
    )
    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
