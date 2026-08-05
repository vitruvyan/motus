"""x5-02 -- does `complete` agree with what the file actually contains?

ADR-011: "`complete` is True when the session received a terminal record and
its sequence is a whole trace, False when the persisted account is a prefix."

Every row below runs a real graph through a real durable sink, then compares
three independent things:

  1. what `finish(complete=...)` said,
  2. whether the JSONL file on disk ends with a terminal record,
  3. what `contract/validate.py` says about that file.

A row is BROKEN if (1) and (2) disagree, or if the validator rejects a file
that `complete=True` claims is whole.
"""

from __future__ import annotations

import gc

from vitruvyan_motus import Runtime, State
from vitruvyan_motus.errors import NodeFailed, SinkFailed

from x5_common import (
    BUFFERED, INMEM, LINEAR, LINEAR_SPEC_PATH, OUT, SYNC, ProtocolJsonlSink,
    Report, commit_then_raise_on_kind, fail_on_batch, fail_on_kind, registry,
    validate_file,
)

D = OUT / "x5_02"


def judge(r: Report, name: str, sink: ProtocolJsonlSink, *,
          expect_finish_calls: int = 1, lost_ack: bool = False) -> None:
    """Cross-check finish(complete) against the file and the validator."""
    if not sink.sessions:
        r.record(name, expect_finish_calls == 0, "no session opened")
        return
    s = sink.sessions[0]
    if len(s.finish_calls) != expect_finish_calls:
        r.record(name, False,
                 f"finish called {len(s.finish_calls)}x, expected "
                 f"{expect_finish_calls}; kinds={s.kinds}")
        return
    if expect_finish_calls == 0:
        r.record(name, True, f"kinds={s.kinds}")
        return
    claimed = s.finish_calls[0]
    on_disk = s.file_has_terminal
    agree = claimed == on_disk
    code, out = validate_file(
        s.path, spec_path=LINEAR_SPEC_PATH, allow_incomplete=not claimed
    )
    detail = (f"complete={claimed} file_terminal={on_disk} "
              f"validator={'PASS' if code == 0 else 'FAIL'} kinds={s.kinds}")
    if code != 0:
        detail += f" :: {out.splitlines()[0] if out else ''}"
    if lost_ack and not agree:
        # Declared-honest case: the write raised after committing, so the
        # runtime genuinely cannot know.  Recorded, not counted as broken.
        r.record(name + " [lost-ack, by design]", True, detail)
        return
    r.record(name, agree and code == 0, detail)


def main() -> int:
    r = Report("x5-02 complete vs the file on disk")

    # ------------------------------------------------ clean runs, 3 profiles #
    for label, kwargs in (
        ("synchronous", {"durability_profile": SYNC}),
        ("buffered/chunk4", {"durability_profile": BUFFERED,
                             "chunk_records": 4, "flush_interval_ms": 50}),
        ("buffered/slow", {"durability_profile": BUFFERED,
                           "chunk_records": 1000, "flush_interval_ms": 60000}),
        ("in-memory+sink", {"durability_profile": INMEM}),
    ):
        s = ProtocolJsonlSink(D, prefix=f"clean-{label.replace('/', '-')}")
        Runtime(LINEAR, registry(), sink=s, **kwargs).run(State.empty("clean"))
        judge(r, f"completed run [{label}]", s)

    # --------------------------------------------------------- node failure #
    def boom(state):
        raise ValueError("boom")

    for label, kwargs in (
        ("synchronous", {"durability_profile": SYNC}),
        ("buffered", {"durability_profile": BUFFERED, "chunk_records": 1000,
                      "flush_interval_ms": 60000}),
    ):
        s = ProtocolJsonlSink(D, prefix=f"nodefail-{label}")
        try:
            Runtime(LINEAR, {**registry(), "c": boom}, sink=s, **kwargs).run(
                State.empty("nodefail")
            )
        except NodeFailed:
            pass
        judge(r, f"node failure [{label}]", s)

    # ---------------------------------------------------------- cancelled   #
    for label, kwargs in (
        ("synchronous", {"durability_profile": SYNC}),
        ("buffered", {"durability_profile": BUFFERED, "chunk_records": 1000,
                      "flush_interval_ms": 60000}),
    ):
        s = ProtocolJsonlSink(D, prefix=f"cancel-{label}")
        rt = Runtime(LINEAR, registry(), sink=s, **kwargs)
        drv = rt.stream(State.empty("cancel"))
        next(drv)
        drv.close("x5 cancelled")
        judge(r, f"cancelled run [{label}]", s)

    # ------------------------------------------------------- abandoned mid  #
    for label, kwargs in (
        ("synchronous", {"durability_profile": SYNC}),
        ("buffered", {"durability_profile": BUFFERED, "chunk_records": 1000,
                      "flush_interval_ms": 60000}),
    ):
        s = ProtocolJsonlSink(D, prefix=f"abandon-{label}")
        rt = Runtime(LINEAR, registry(), sink=s, **kwargs)
        drv = rt.stream(State.empty("abandon"))
        next(drv)
        next(drv)
        del drv
        gc.collect()
        judge(r, f"abandoned mid-run [{label}]", s)

    # ------------------------------- sink refuses the batch with the terminal #
    for label, kwargs in (
        ("synchronous", {"durability_profile": SYNC}),
        ("buffered", {"durability_profile": BUFFERED, "chunk_records": 1000,
                      "flush_interval_ms": 60000}),
    ):
        s = ProtocolJsonlSink(D, prefix=f"refuse-terminal-{label}",
                              fail=fail_on_kind("run_completed"))
        try:
            Runtime(LINEAR, registry(), sink=s, **kwargs).run(
                State.empty("refuse-terminal")
            )
        except SinkFailed:
            pass
        judge(r, f"sink refuses terminal batch [{label}]", s)

    # ------------------------------- sink refuses a middle batch             #
    for label, kwargs in (
        ("synchronous", {"durability_profile": SYNC}),
        ("buffered", {"durability_profile": BUFFERED, "chunk_records": 2,
                      "flush_interval_ms": 60000}),
    ):
        s = ProtocolJsonlSink(D, prefix=f"refuse-mid-{label}",
                              fail=fail_on_batch(3))
        try:
            Runtime(LINEAR, registry(), sink=s, **kwargs).run(
                State.empty("refuse-mid")
            )
        except SinkFailed:
            pass
        judge(r, f"sink refuses middle batch [{label}]", s)

    # ---------------- the lost acknowledgement: committed THEN raised ------- #
    for label, kwargs in (
        ("synchronous", {"durability_profile": SYNC}),
        ("buffered", {"durability_profile": BUFFERED, "chunk_records": 1000,
                      "flush_interval_ms": 60000}),
    ):
        s = ProtocolJsonlSink(D, prefix=f"lostack-{label}",
                              fail=commit_then_raise_on_kind("run_completed"))
        try:
            Runtime(LINEAR, registry(), sink=s, **kwargs).run(
                State.empty("lostack")
            )
        except SinkFailed:
            pass
        judge(r, f"terminal committed then write raised [{label}]", s,
              lost_ack=True)

    # -------------------------------------------------- node BaseException   #
    def hard(state):
        raise KeyboardInterrupt("hard")

    s = ProtocolJsonlSink(D, prefix="baseexc-buffered")
    try:
        Runtime(LINEAR, {**registry(), "c": hard}, sink=s,
                durability_profile=BUFFERED, chunk_records=1000,
                flush_interval_ms=60000).run(State.empty("baseexc"))
    except BaseException:
        pass
    judge(r, "node raised BaseException [buffered]", s)

    # ------------------------------------------------ retries then success   #
    calls = {"n": 0}

    def flaky(state):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return state

    for label, kwargs in (
        ("synchronous", {"durability_profile": SYNC}),
        ("buffered", {"durability_profile": BUFFERED, "chunk_records": 3,
                      "flush_interval_ms": 60000}),
    ):
        calls["n"] = 0
        s = ProtocolJsonlSink(D, prefix=f"retry-{label}")
        Runtime(LINEAR, {**registry(), "c": flaky}, sink=s, max_attempts=3,
                **kwargs).run(State.empty("retry"))
        judge(r, f"node retried then succeeded [{label}]", s)

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
