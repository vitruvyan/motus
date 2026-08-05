"""x2b-04 -- quantify X2-004's durable damage (still open by decision).

The question the coordinator asked: what exactly does a ``TraceSink`` see when
a driver is created and never advanced, and is the persisted artifact
contract-valid?

``_start`` opens the durable session *before* it returns the generator:

    self._trace = Trace(header)
    self._trace_ref = [self._trace]
    self._hub.bind(self._trace.run)          # <- sink.open_run(header) HERE
    return self._managed_execute(...)

So the sink is told a run began, is handed a complete and well-formed header
carrying a real ``run_id``, and then never receives a single record.  A sink
that materialises what it was given produces ``{"schema_version": ..., "run":
{...}, "records": []}`` -- which contract fixture 20 declares invalid ("should
be non-empty").  The artifact is therefore not merely incomplete; it cannot be
validated, replayed, or reconciled, and nothing in it says so.
"""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import json
import sys
from pathlib import Path

from vitruvyan_motus import TRACE_SCHEMA_VERSION, GraphSpec, Runtime, State
from x2b_common import LINEAR, LINEAR_DOC, Report, anode, snode

ROOT = Path("/home/vitruvyan/motus")
R = Report("x2b_04 orphaned durable session")


def _validator():
    spec = importlib.util.spec_from_file_location(
        "motus_contract_validate_x2b", ROOT / "contract" / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate = _validator()


class MaterialisingSink:
    """A sink that does what a real durable sink does: persist what it is told."""

    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    def open_run(self, header):
        session = _Session(header)
        self.sessions.append(session)
        return session

    def documents(self) -> list[dict]:
        return [
            {
                "schema_version": TRACE_SCHEMA_VERSION,
                "run": s.header,
                "records": list(s.records),
            }
            for s in self.sessions
        ]


class _Session:
    def __init__(self, header) -> None:
        self.header = header
        self.records: list = []
        self.write_calls = 0

    def write(self, records) -> None:
        self.write_calls += 1
        self.records.extend(records)


def case_what_the_sink_sees() -> None:
    sink = MaterialisingSink()
    rt = Runtime(
        LINEAR, {"a": snode, "b": snode, "c": snode},
        durability_profile="synchronous", sink=sink,
    )
    rt.stream(State.empty("abandoned"), run_id="orphan-run-0001")
    gc.collect()
    session = sink.sessions[0]
    header_keys = sorted(session.header)
    R.record(
        "sink is told a run began and then nothing",
        session.write_calls > 0,
        f"open_run_calls={len(sink.sessions)} write_calls={session.write_calls} "
        f"records={len(session.records)} run_id={session.header['run_id']!r} "
        f"header_keys={header_keys}",
    )


def case_artifact_is_contract_invalid() -> None:
    sink = MaterialisingSink()
    rt = Runtime(
        LINEAR, {"a": snode, "b": snode, "c": snode},
        durability_profile="synchronous", sink=sink,
    )
    rt.stream(State.empty("abandoned"), run_id="orphan-run-0002")
    gc.collect()
    document = sink.documents()[0]
    violations = validate.validate_trace(document, spec=LINEAR_DOC)
    rules = sorted({getattr(v, "rule", str(v)) for v in violations})
    messages = [str(getattr(v, "reason", v))[:70] for v in violations[:3]]
    R.record(
        "persisted artifact validates",
        not violations,
        f"violations={len(violations)} rules={rules} sample={messages}",
    )


def case_orphan_is_indistinguishable_from_a_crash() -> None:
    """A crashed run and an abandoned driver leave the sink the same evidence,
    so an operator cannot tell 'the process died mid-run' from 'a caller built
    a driver and dropped it' -- and only one of those is a real incident."""
    crashed = MaterialisingSink()
    rt1 = Runtime(
        LINEAR, {"a": snode, "b": snode, "c": snode},
        durability_profile="synchronous", sink=crashed,
    )
    driver = rt1.stream(State.empty("crash"), run_id="crashed-run")
    # nothing consumed: identical to a process that died before the first record
    abandoned = MaterialisingSink()
    rt2 = Runtime(
        LINEAR, {"a": snode, "b": snode, "c": snode},
        durability_profile="synchronous", sink=abandoned,
    )
    rt2.stream(State.empty("abandon"), run_id="abandoned-run")
    del driver
    gc.collect()
    a = crashed.documents()[0]
    b = abandoned.documents()[0]
    same_shape = (
        a["records"] == b["records"] == []
        and sorted(a["run"]) == sorted(b["run"])
    )
    R.record(
        "orphan distinguishable from a crash truncation",
        not same_shape,
        f"both_empty={a['records'] == b['records'] == []} "
        "identical_shape={} (no marker records the difference)".format(same_shape),
    )


def case_wedge_is_permanent() -> None:
    """Confirm the wedge cannot be recovered by any public means."""
    rt = Runtime(LINEAR, {"a": snode, "b": snode, "c": snode})
    rt.stream(State.empty("abandoned"))
    gc.collect()
    attempts: dict[str, str] = {}
    for label, call in (
        ("run", lambda: rt.run(State.empty("x"))),
        ("stream", lambda: rt.stream(State.empty("x"))),
        ("arun", lambda: asyncio.run(rt.arun(State.empty("x")))),
        ("astream", lambda: rt.astream(State.empty("x"))),
    ):
        try:
            call()
            attempts[label] = "ok"
        except BaseException as exc:  # noqa: BLE001
            attempts[label] = type(exc).__name__
    cancel_says = rt.cancel("try to unwedge")
    gc.collect()
    still = rt._running
    R.record(
        "wedge is recoverable",
        all(v == "ok" for v in attempts.values()),
        f"entry_points={attempts} cancel_returned={cancel_says} "
        f"still_running_after_gc={still} "
        "(cancel() reports True for a run that does not exist)",
    )


def case_astream_variant() -> None:
    sink = MaterialisingSink()

    async def go() -> None:
        rt = Runtime(
            LINEAR, {"a": anode, "b": anode, "c": anode},
            durability_profile="synchronous", sink=sink,
        )
        rt.astream(State.empty("abandoned"), run_id="orphan-async")
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(go())
    document = sink.documents()[0]
    violations = validate.validate_trace(document, spec=LINEAR_DOC)
    R.record(
        "astream orphan artifact validates",
        not violations,
        f"records={len(document['records'])} violations={len(violations)}",
    )


def main() -> int:
    case_what_the_sink_sees()
    case_artifact_is_contract_invalid()
    case_astream_variant()
    case_orphan_is_indistinguishable_from_a_crash()
    case_wedge_is_permanent()
    return R.dump()


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
