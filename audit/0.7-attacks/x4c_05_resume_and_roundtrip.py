"""x4c-05 — Resume segments and encoding round trips.

`ReplayEngine.resume` refuses a terminal trace, so its ONLY legal input is a
trace that stops before its terminal — i.e. exactly the artifact shape that
`contract/validate.py` rejects without `--allow-incomplete`.  This script
therefore does the whole loop the way an operator would have to:

  run -> abandoned mid-flight -> persisted JSONL (incomplete) -> reload ->
  TraceBundle -> resume into a second Runtime with its own sink -> validate
  BOTH artifacts, and check that the pair is a coherent, linked account.

Also pins the encodings: trace -> JSON -> Trace.from_dict -> validate, and
trace -> JSONL -> validate_jsonl -> reassembled doc equals the JSON document.
"""

from __future__ import annotations

import gc
import json
from pathlib import Path

from vitruvyan_motus import (
    DurabilityProfile, GraphSpec, ReplayEngine, Runtime, State, Trace,
    TraceBundle,
)

from x4c_common import (
    LINEAR, LINEAR_DOC, LINEAR_SPEC_PATH, OUT, JsonlFileSink, Report,
    kinds_of_file, registry, validate_file, write_json, write_text,
)


def load_jsonl(path: Path) -> Trace:
    lines = [l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]
    header = json.loads(lines[0])
    records = [json.loads(l) for l in lines[1:]]
    return Trace.from_dict({
        "schema_version": header["schema_version"],
        "run": header["run"],
        "records": records,
    })


def main() -> int:
    r = Report("x4c-05 resume artifacts and round trips")

    # --- 1. produce a resumable (= incomplete) persisted artifact ---------- #
    sink1 = JsonlFileSink(OUT / "05" / "source", prefix="src")
    rt1 = Runtime(LINEAR, registry(),
                  durability_profile=DurabilityProfile.SYNCHRONOUS, sink=sink1)
    d = rt1.stream()
    for i, _ in enumerate(d):
        if i >= 6:  # stop just after b's routing
            break
    del d
    gc.collect()
    src_path = sink1.sessions[0].path
    code, out = validate_file(src_path, "jsonl", spec_path=LINEAR_SPEC_PATH)
    r.record("source artifact rejected as complete", code != 0,
             f"rc={code} {out.splitlines()[0][:110] if out else 'VALID'}")
    code_i, out_i = validate_file(src_path, "jsonl", spec_path=LINEAR_SPEC_PATH,
                                  allow_incomplete=True)
    r.record("source artifact accepted with --allow-incomplete", code_i == 0,
             f"rc={code_i} {out_i.splitlines()[0][:110] if out_i else 'VALID'}; "
             f"records={kinds_of_file(src_path)[1:]}")

    # --- 2. reload it and resume ------------------------------------------ #
    source_trace = load_jsonl(src_path)
    bundle = TraceBundle(GraphSpec.from_dict(dict(LINEAR_DOC)), source_trace)
    engine = ReplayEngine(bundle)

    sink2 = JsonlFileSink(OUT / "05" / "segment", prefix="seg")
    rt2 = Runtime(LINEAR, registry(),
                  durability_profile=DurabilityProfile.SYNCHRONOUS, sink=sink2)
    result = engine.resume(rt2, run_id="x4c-resume-segment-1")
    seg_path = sink2.sessions[0].path
    code, out = validate_file(seg_path, "jsonl", spec_path=LINEAR_SPEC_PATH)
    r.record("resume segment JSONL validates", code == 0,
             f"status={result.status} rc={code} "
             f"{out.splitlines()[0][:140] if out else 'VALID'}")

    seg_doc = write_json("05-segment-doc.json", result.trace.to_dict())
    code, out = validate_file(seg_doc, "trace", spec_path=LINEAR_SPEC_PATH)
    r.record("resume segment JSON document validates", code == 0,
             f"rc={code} {out.splitlines()[0][:140] if out else 'VALID'}")

    # --- 3. the resume header block, through both encodings --------------- #
    memory_resume = result.trace.run.get("resume")
    sink_resume = sink2.headers[0].get("resume")
    file_resume = json.loads(seg_path.read_text(encoding="utf-8").split("\n")[0])[
        "run"].get("resume")
    reloaded = load_jsonl(seg_path)
    r.record("resume block reaches the sink header", sink_resume == memory_resume,
             f"{sink_resume}")
    r.record("resume block survives JSONL", file_resume == memory_resume,
             f"{file_resume}")
    r.record("resume block survives Trace.from_dict",
             reloaded.run.get("resume") == memory_resume, "")
    r.record("resume names the source run",
             (memory_resume or {}).get("source_run_id") == source_trace.run["run_id"]
             and (memory_resume or {}).get("start_node") == "c",
             f"source_run_id ok, start_node={(memory_resume or {}).get('start_node')}, "
             f"source_seq={(memory_resume or {}).get('source_seq')}")

    # --- 4. bundle fingerprint stability across the file round trip ------- #
    reloaded_bundle = TraceBundle(GraphSpec.from_dict(dict(LINEAR_DOC)), reloaded)
    in_memory_bundle = TraceBundle(GraphSpec.from_dict(dict(LINEAR_DOC)),
                                   result.trace)
    r.record("bundle fingerprint survives the file round trip",
             reloaded_bundle.fingerprint == in_memory_bundle.fingerprint,
             reloaded_bundle.fingerprint[:40] + "...")
    r.record("TraceBundle.from_json round trip",
             TraceBundle.from_json(in_memory_bundle.to_json()).fingerprint
             == in_memory_bundle.fingerprint, "")

    # --- 5. does the recorded bundle_fingerprint address the SOURCE? ------ #
    r.record("resume.bundle_fingerprint == the source bundle's fingerprint",
             (memory_resume or {}).get("bundle_fingerprint") == bundle.fingerprint,
             f"{(memory_resume or {}).get('bundle_fingerprint', '')[:40]}...")

    # --- 6. JSON <-> JSONL equivalence on a normal complete run ----------- #
    sink3 = JsonlFileSink(OUT / "05" / "plain", prefix="p")
    rt3 = Runtime(LINEAR, registry(),
                  durability_profile=DurabilityProfile.SYNCHRONOUS, sink=sink3)
    res3 = rt3.run()
    doc = res3.trace.to_dict()
    jsonl_path = write_text("05-plain.jsonl", res3.trace.to_jsonl())
    code_a, _ = validate_file(write_json("05-plain.json", doc), "trace",
                              spec_path=LINEAR_SPEC_PATH)
    code_b, out_b = validate_file(jsonl_path, "jsonl", spec_path=LINEAR_SPEC_PATH)
    reassembled = load_jsonl(jsonl_path).to_dict()
    r.record("Trace.to_jsonl validates", code_b == 0, f"rc={code_b} {out_b[:110]}")
    r.record("JSON doc == JSONL reassembly", reassembled == doc,
             "byte-equal documents" if reassembled == doc else "DIVERGED")
    r.record("sink file == Trace.to_jsonl",
             sink3.sessions[0].path.read_bytes() == jsonl_path.read_bytes(),
             "the sink's own stream is the canonical encoding")

    # --- 7. resuming a resume ---------------------------------------------- #
    sink4 = JsonlFileSink(OUT / "05" / "segment2", prefix="seg2")
    rt4 = Runtime(LINEAR, registry(),
                  durability_profile=DurabilityProfile.SYNCHRONOUS, sink=sink4)
    d4 = rt4.stream()
    # resume the SEGMENT after truncating it the same way
    sink5 = JsonlFileSink(OUT / "05" / "seg-trunc", prefix="t")
    rt5 = Runtime(LINEAR, registry(),
                  durability_profile=DurabilityProfile.SYNCHRONOUS, sink=sink5)
    e5 = ReplayEngine(bundle).resume(rt5, run_id="x4c-resume-chain-a")
    del d4
    gc.collect()
    try:
        chain = TraceBundle(GraphSpec.from_dict(dict(LINEAR_DOC)),
                            load_jsonl(sink5.sessions[0].path))
        ReplayEngine(chain).resume(rt4, run_id="x4c-resume-chain-b")
        r.record("chained resume of a completed segment refused", False,
                 "a terminal segment was resumed")
    except Exception as exc:  # noqa: BLE001
        r.record("chained resume of a completed segment refused", True,
                 f"{type(exc).__name__}: {exc}")

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
