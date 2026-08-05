"""x5-03 -- is the header the right object, isolated, and complete?

ADR-011 Decision 1: `open_run` receives `{schema_version, run}`, and
`Trace.header` is the public accessor.  ADR-004 Verification 3 (unamended):
"header mutation by a sink cannot alter the returned Trace".

Also: resume segments (`ReplayEngine.resume` -> `Runtime._run_from`) carry an
extra `resume` block; the sink must see it.  And `InMemoryTraceSink`'s
accessors changed shape underneath their callers.
"""

from __future__ import annotations

import copy
import json

from vitruvyan_motus import (
    DurabilityProfile, InMemoryTraceSink, ReplayEngine, Runtime, State,
    TRACE_SCHEMA_VERSION,
)

from x5_common import (
    BUFFERED, LINEAR, LINEAR_SPEC_PATH, OUT, SYNC, ProtocolJsonlSink, Report,
    registry, validate_file,
)

D = OUT / "x5_03"


class MutatingSink:
    """A hostile sink: mutates everything it is handed, every way it can."""

    def __init__(self) -> None:
        self.headers = []
        self.errors = []

    def open_run(self, header):
        self.headers.append(copy.deepcopy(header))  # as delivered, pre-mutation
        for attempt in (
            lambda: header.__setitem__("schema_version", "666.6.6"),
            lambda: header.__setitem__("INJECTED", True),
            lambda: header["run"].__setitem__("run_id", "hijacked"),
            lambda: header["run"].__setitem__("INJECTED", True),
            lambda: header["run"]["graph"].__setitem__("name", "hijacked"),
            lambda: dict.__setitem__(header["run"], "raw_injected", 1),
            lambda: dict.__setitem__(header, "raw_top", 1),
        ):
            try:
                attempt()
            except BaseException as exc:  # noqa: BLE001
                self.errors.append(repr(exc))
        return _Session()


class _Session:
    def __init__(self) -> None:
        self.records = []

    def write(self, records):
        self.records.extend(records)

    def finish(self, *, complete):
        self.complete = complete


def main() -> int:
    r = Report("x5-03 header shape, isolation and resume")

    # ---------------------------------------- 1. shape equals Trace.header  #
    s = ProtocolJsonlSink(D, prefix="shape")
    result = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=s).run(
        State.empty("shape")
    )
    got = s.headers[0]
    r.record(
        "open_run receives {schema_version, run}",
        set(got) == {"schema_version", "run"}
        and got == result.trace.header
        and got["schema_version"] == TRACE_SCHEMA_VERSION,
        f"keys={sorted(got)} equal_to_trace.header={got == result.trace.header}",
    )

    # -------------------------- 2. header + records == the trace document   #
    doc = result.trace.to_dict()
    r.record(
        "header is the document minus records",
        {k: v for k, v in doc.items() if k != "records"} == got,
        f"document minus records == open_run header: "
        f"{ {k: v for k, v in doc.items() if k != 'records'} == got }",
    )

    # ---------------------------- 3. the JSONL file the sink wrote validates #
    code, out = validate_file(s.sessions[0].path, spec_path=LINEAR_SPEC_PATH)
    r.record(
        "protocol-only JSONL artifact validates",
        code == 0,
        out.splitlines()[0] if out else "clean",
    )

    # ---------------------------- 4. jsonl <-> json document equivalence     #
    lines = s.sessions[0].lines
    rebuilt = {**lines[0], "records": lines[1:]}
    r.record(
        "JSONL rebuilt == Trace.to_dict()",
        rebuilt == doc,
        "structurally equal" if rebuilt == doc else "DIFFERS",
    )

    # ---------------------------- 5. hostile sink cannot touch the trace     #
    m = MutatingSink()
    res2 = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=m).run(
        State.empty("mutate"), run_id="pristine"
    )
    r.record(
        "sink mutation cannot alter the Trace",
        res2.trace.run["run_id"] == "pristine"
        and res2.trace.header["schema_version"] == TRACE_SCHEMA_VERSION
        and "INJECTED" not in res2.trace.header
        and "INJECTED" not in res2.trace.run
        and "raw_injected" not in res2.trace.run
        and res2.trace.run["graph"]["name"] == LINEAR.name,
        f"run_id={res2.trace.run['run_id']} "
        f"schema={res2.trace.header['schema_version']} "
        f"top_keys={sorted(res2.trace.header)} "
        f"mutation_errors={len(m.errors)}",
    )

    # ------------------- 6. hostile sink cannot poison the NEXT run's header #
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=m)
    rt.run(State.empty("first"), run_id="alpha")
    res3 = rt.run(State.empty("second"), run_id="beta")
    r.record(
        "a mutated header does not leak into the next run",
        res3.trace.run["run_id"] == "beta"
        and "INJECTED" not in res3.trace.header
        and m.headers[-1]["run"]["run_id"] == "beta",
        f"run2 run_id={res3.trace.run['run_id']} "
        f"header seen by sink={m.headers[-1]['run']['run_id']}",
    )

    # ------------------- 7. Trace.header returns a fresh plain object        #
    t = res3.trace
    h1, h2 = t.header, t.header
    h1["run"]["run_id"] = "tampered"
    r.record(
        "Trace.header is a fresh deep copy each call",
        h1 is not h2 and h2["run"]["run_id"] == "beta"
        and t.run["run_id"] == "beta"
        and type(h1) is dict and type(h1["run"]) is dict,
        f"second call run_id={h2['run']['run_id']} types="
        f"{type(h1).__name__}/{type(h1['run']).__name__}",
    )

    # ------------------- 8. json.dumps works on what the sink is handed      #
    try:
        json.dumps(m.headers[-1], allow_nan=False)
        dumpable = True
        err = ""
    except BaseException as exc:  # noqa: BLE001
        dumpable, err = False, repr(exc)
    r.record("the delivered header is JSON-serialisable", dumpable, err)

    # ------------------- 9. RESUME segment: the header carries `resume`      #
    # A resumable source is a PREFIX: abandon a stream after a routing record,
    # which is exactly the artifact `finish(complete=False)` now labels.
    import gc
    src_sink = ProtocolJsonlSink(D, prefix="resume-src")
    src_rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=src_sink)
    drv = src_rt.stream(State.empty("src"), run_id="source-run")
    for rec in drv:
        if rec["kind"] == "routing":
            break
    del drv, rec
    gc.collect()
    r.record(
        "abandoned prefix source: session told complete=False",
        src_sink.sessions[0].finish_calls == [False],
        f"finish={src_sink.sessions[0].finish_calls} "
        f"kinds={src_sink.sessions[0].kinds}",
    )
    lines = src_sink.sessions[0].lines
    from vitruvyan_motus import Trace, TraceBundle
    source_trace = Trace.from_dict({**lines[0], "records": lines[1:]})

    seg_sink = ProtocolJsonlSink(D, prefix="resume-seg")
    seg_rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=seg_sink)
    engine = ReplayEngine(TraceBundle(LINEAR, source_trace))
    seg = engine.resume(seg_rt, run_id="segment-run")
    seg_header = seg_sink.headers[0]
    r.record(
        "resume segment header reaches the sink with its `resume` block",
        set(seg_header) == {"schema_version", "run"}
        and "resume" in seg_header["run"]
        and seg_header["run"]["resume"].get("source_run_id") == "source-run"
        and seg_header == seg.trace.header,
        f"keys={sorted(seg_header)} run_keys_has_resume="
        f"{'resume' in seg_header['run']} "
        f"resume={seg_header['run'].get('resume')}",
    )
    code, out = validate_file(seg_sink.sessions[0].path,
                              spec_path=LINEAR_SPEC_PATH)
    r.record(
        "resume-segment JSONL artifact validates",
        code == 0 and seg_sink.sessions[0].finish_calls == [True],
        f"finish={seg_sink.sessions[0].finish_calls} "
        + (out.splitlines()[0] if out else "clean"),
    )

    # ------------------- 10. InMemoryTraceSink accessors after the change    #
    ims = InMemoryTraceSink()
    rt = Runtime(LINEAR, registry(), durability_profile=SYNC, sink=ims)
    a = rt.run(State.empty("ims-a"), run_id="ims-a")
    b = rt.run(State.empty("ims-b"), run_id="ims-b")
    r.record(
        "InMemoryTraceSink.header is the TraceHeader of the LAST run",
        ims.header == b.trace.header
        and set(ims.header) == {"schema_version", "run"},
        f"keys={sorted(ims.header)} run_id={ims.header['run']['run_id']}",
    )
    r.record(
        "InMemoryTraceSink.records is the last run's records",
        ims.records == b.trace.records,
        f"{len(ims.records)} records, last={ims.records[-1]['kind']}",
    )
    r.record(
        "InMemoryTraceSink.runs partitions by run",
        [run["header"]["run"]["run_id"] for run in ims.runs] == ["ims-a", "ims-b"]
        and all(run["records"][-1]["kind"] == "run_completed" for run in ims.runs),
        f"{[run['header']['run']['run_id'] for run in ims.runs]}",
    )
    r.record(
        "InMemoryTraceSink header is isolated from mutation",
        (lambda h: (h["run"].__setitem__("run_id", "x"),
                    ims.header["run"]["run_id"] == "ims-b")[1])(ims.header),
        f"after mutation: {ims.header['run']['run_id']}",
    )
    ses = ims._runs[-1]
    r.record(
        "_InMemoryRunSink has no finish, so none is called",
        not hasattr(ses, "finish"),
        f"has finish: {hasattr(ses, 'finish')}",
    )

    # ------------- 11. the OLD (x4c-shaped) sink under the new protocol ---- #
    class OldShapeSink:
        """Written against the pre-ADR-011 protocol: wraps what it is given."""

        def __init__(self, path):
            self.path = path

        def open_run(self, header):
            self.fh = open(self.path, "w", encoding="utf-8", newline="\n")
            self.fh.write(json.dumps(
                {"schema_version": TRACE_SCHEMA_VERSION, "run": header}) + "\n")
            return self

        def write(self, records):
            for rec in records:
                self.fh.write(json.dumps(rec) + "\n")
            self.fh.flush()

    old_path = D / "old-shape.jsonl"
    D.mkdir(parents=True, exist_ok=True)
    Runtime(LINEAR, registry(), durability_profile=SYNC,
            sink=OldShapeSink(old_path)).run(State.empty("old"))
    code, out = validate_file(old_path, spec_path=LINEAR_SPEC_PATH)
    first = out.splitlines()[0] if out else ""
    r.record(
        "old-protocol sink now produces an artifact the validator REJECTS "
        "(intended break, loudly)",
        code == 1 and "TraceHeader" in out,
        f"exit={code} :: {first}",
    )

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
