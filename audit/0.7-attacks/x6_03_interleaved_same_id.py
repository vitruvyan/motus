"""Attack: two live sessions on one path.  Deterministic, single-threaded.

`open(path, "w")` is O_TRUNC.  When a second session for the same run_id opens
the same `<stem>.jsonl.part`, it truncates the first session's file *under* the
first session's still-open descriptor -- whose offset does not move.  Everything
the first session writes from then on lands past a hole the kernel fills with
NUL bytes.

Driven entirely through the public runtime API (`Runtime.stream`), interleaved
on one thread, so there is no race to argue about.
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from x6_common import (  # noqa: E402
    JsonlTraceSink, Report, State, listing, runtime, scratch, spec_path,
    validate_expected, validate_file,
)


def main() -> int:
    rep = Report("x6_03 interleaved sessions on one path")
    directory = scratch("interleaved")
    sink = JsonlTraceSink(directory, fsync=False)

    # Two Runtimes -- nothing in the contract makes run_id unique, and nothing
    # in the runtime rejects the repeat (a retried job keeps its job id).
    first = runtime(sink=sink)
    second = runtime(sink=sink)

    d1 = first.stream(State.empty("FIRST"), run_id="dup")
    next(d1)                       # session 1: header + records in .part
    next(d1)

    d2 = second.stream(State.empty("SECOND"), run_id="dup")   # O_TRUNC
    rep.note(f"after second open: {listing(directory)}")

    next(d1)                       # session 1 keeps writing at its old offset
    next(d2)

    d1.close("done")               # -> renames .part to .jsonl
    rep.note(f"after first close: {listing(directory)}")
    d2.close("done")               # -> renames what is now a fresh .part
    rep.note(f"after second close: {listing(directory)}")

    files = listing(directory)
    rep.record("both runs kept a file", len(files) == 2, f"files={files}")

    for name in files:
        path = directory / name
        raw = path.read_bytes()
        holes = raw.count(b"\x00")
        rep.record(
            f"{name[:28]} has no NUL padding",
            holes == 0,
            f"{holes} NUL bytes in {len(raw)}; head={raw[:40]!r}",
        )
        ok, out = validate_expected(path, spec=spec_path())
        rep.record(f"{name[:28]} validates", ok, out[:400])

    rep.note(f"sink.artifacts = {[str(p) for p in sink.artifacts]}")
    rep.record(
        "every path sink.artifacts names exists",
        all(p.exists() for p in sink.artifacts),
        f"missing={[str(p) for p in sink.artifacts if not p.exists()]}",
    )
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(main())
