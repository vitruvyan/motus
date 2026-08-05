"""x4c-09 — What signals does a run-scoped session actually receive?

The ADR-004 question in its sharpest form.  A durable sink must decide, at
some moment, that a file is finished.  This records EVERY attribute the
runtime touches on a `TraceRunSink`, on the happy path and on the abandoned
path, so the decision has the real surface in front of it.
"""

from __future__ import annotations

import gc

from vitruvyan_motus import DurabilityProfile, Runtime

from x4c_common import LINEAR, Report, registry


class ProbeRunSink:
    def __init__(self, log: list) -> None:
        self._log = log

    def __getattr__(self, name):           # only for attributes not defined here
        self._log.append(("getattr", name))
        raise AttributeError(name)

    def write(self, records):
        self._log.append(("write", tuple(r["kind"] for r in records)))


class ProbeSink:
    def __init__(self) -> None:
        self.log: list = []

    def open_run(self, header):
        self.log.append(("open_run", sorted(header)))
        return ProbeRunSink(self.log)


def calls(log):
    return [entry for entry in log if entry[0] != "write"] + [
        ("write", sum(1 for e in log if e[0] == "write"))
    ]


def main() -> int:
    r = Report("x4c-09 run-session lifecycle signals")

    # happy path
    s = ProbeSink()
    Runtime(LINEAR, registry(), durability_profile=DurabilityProfile.SYNCHRONOUS,
            sink=s).run()
    non_write = [e for e in s.log if e[0] != "write"]
    last_write = [e for e in s.log if e[0] == "write"][-1]
    r.record(
        "completed run: session is told the run ended", False,
        f"non-write calls on the session: "
        f"{[e for e in non_write if e[0] != 'open_run'] or 'NONE'}; "
        f"last write = {last_write[1]} — the ONLY end-of-run signal is the "
        f"sink recognising the record kind itself",
    )

    # abandoned path
    s2 = ProbeSink()
    rt2 = Runtime(LINEAR, registry(),
                  durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s2)
    d = rt2.stream()
    for i, _ in enumerate(d):
        if i >= 3:
            break
    del d
    gc.collect()
    writes = [e for e in s2.log if e[0] == "write"]
    r.record(
        "abandoned run: session is told anything at all", False,
        f"{len(writes)} write batches, last = {writes[-1][1] if writes else None}; "
        f"other calls = {[e for e in s2.log if e[0] not in ('write', 'open_run')] or 'NONE'} "
        f"-- the session cannot distinguish 'in flight' from 'abandoned forever'",
    )

    # never-advanced path
    s3 = ProbeSink()
    rt3 = Runtime(LINEAR, registry(),
                  durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s3)
    d3 = rt3.stream()
    del d3
    gc.collect()
    r.record(
        "never-advanced run: session receives any record", False,
        f"calls = {s3.log} -- open_run happened, nothing followed",
    )

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
