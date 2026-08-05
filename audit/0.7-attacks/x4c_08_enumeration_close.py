"""x4c-08 — Closing the enumeration: the remaining ways a session opens.

Covers what x4c-02 did not:
  * an event loop that dies before finalising an abandoned async driver;
  * runs that fail BEFORE `open_run` (no orphan session — the clean case);
  * session accounting across sequential runs on one Runtime;
  * `InMemoryTraceSink` accepted under profiles whose crash guarantee it
    explicitly disclaims, and stamped into the header as such.
"""

from __future__ import annotations

import asyncio
import gc
import json

from vitruvyan_motus import (
    DurabilityProfile, InMemoryTraceSink, Runtime, State,
)

from x4c_common import (
    LINEAR, LINEAR_SPEC_PATH, OUT, JsonlFileSink, Report, kinds_of_file,
    registry, validate_file,
)


def main() -> int:
    r = Report("x4c-08 enumeration close")
    SYNC = DurabilityProfile.SYNCHRONOUS

    # --- 1. loop dies before finalising an abandoned async driver --------- #
    s1 = JsonlFileSink(OUT / "08" / "loop-death", prefix="l")
    rt1 = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s1)

    async def go():
        d = rt1.astream()
        i = 0
        async for _ in d:
            i += 1
            if i >= 4:
                break
        return d  # deliberately returned, i.e. survives the loop

    loop = asyncio.new_event_loop()
    driver = loop.run_until_complete(go())
    loop.close()                       # NO shutdown_asyncgens
    del driver
    gc.collect()
    persisted = kinds_of_file(s1.sessions[0].path)[1:]
    code, out = validate_file(s1.sessions[0].path, "jsonl", spec_path=LINEAR_SPEC_PATH)
    r.record(
        "async driver abandoned after loop.close()", code == 0,
        f"_running={rt1._running} persisted={persisted} rc={code} :: "
        f"{out.splitlines()[0][:100] if out else 'VALID'}",
    )

    # --- 2. failures BEFORE open_run leave no orphan session -------------- #
    for label, arg, kw in (
        ("run_id too long", None, {"run_id": "x" * 201}),
        ("state is not a State", "not a State", {}),
    ):
        s2 = JsonlFileSink(OUT / "08" / f"pre-open-{label[:12]}", prefix="p")
        rt2 = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s2)
        try:
            rt2.run(arg, **kw)
            got = "no error"
        except BaseException as exc:  # noqa: BLE001
            got = type(exc).__name__
        r.record(f"pre-open failure leaves no session ({label})",
                 s2.open_calls == 0, f"{got}; open_calls={s2.open_calls}")

    # An empty run_id is silently replaced rather than refused, despite the
    # stated 1..200 validation — noted, not a durability defect.
    s2b = JsonlFileSink(OUT / "08" / "empty-run-id", prefix="e")
    rt2b = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s2b)
    rt2b.run(run_id="")
    r.record("empty run_id refused by the 1..200 check", False,
             f"accepted; header run_id={s2b.headers[0]['run_id']!r} "
             f"(`run_id or kernel_uuid()` treats '' as absent)")

    # --- 3. session accounting across sequential runs --------------------- #
    s3 = JsonlFileSink(OUT / "08" / "sequential", prefix="q")
    rt3 = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s3)
    d = rt3.stream()          # session 1 — abandoned before first advance
    del d
    gc.collect()
    rt3.run()                 # session 2 — complete
    rt3.run()                 # session 3 — complete
    verdicts = []
    for path in s3.paths:
        code, _ = validate_file(path, "jsonl", spec_path=LINEAR_SPEC_PATH)
        verdicts.append((path.name, len(kinds_of_file(path)) - 1, code))
    r.record(
        "sequential runs: every opened session is complete",
        all(c == 0 for _, _, c in verdicts),
        f"{verdicts}  (a reader of this directory sees 3 runs, 1 of which "
        f"has no account)",
    )

    # --- 4. run_ids: can a reader tell the empty session apart? ----------- #
    ids = [h["run_id"] for h in s3.headers]
    r.record(
        "the empty session names a run_id that has no records",
        len(set(ids)) == len(ids) and len(ids) == 3,
        f"{[i[:8] for i in ids]} — headers exist for all three",
    )

    # --- 5. the only shipped sink under a crash-guarantee profile --------- #
    mem = InMemoryTraceSink()
    rt5 = Runtime(LINEAR, registry(),
                  durability_profile=DurabilityProfile.SYNCHRONOUS, sink=mem)
    res5 = rt5.run()
    header = res5.trace.run
    r.record(
        "InMemoryTraceSink refused under `synchronous`", False,
        f"accepted; header states durability_profile="
        f"{header['durability_profile']!r} sink={header.get('sink')} — the "
        f"docstring of the sink says 'no crash-persistence promise'",
    )
    rt5b = Runtime(LINEAR, registry(),
                   durability_profile=DurabilityProfile.BUFFERED,
                   sink=InMemoryTraceSink(), chunk_records=8,
                   flush_interval_ms=500)
    hdr = rt5b.run().trace.run
    r.record(
        "InMemoryTraceSink refused under `buffered`", False,
        f"accepted; header discloses a loss window "
        f"{hdr.get('sink')} for a sink that survives nothing",
    )

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
