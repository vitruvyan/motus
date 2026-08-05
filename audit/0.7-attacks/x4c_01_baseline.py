"""x4c-01 — Does a healthy run through a real file sink produce a valid artifact?

Establishes the control before any attack: synchronous and buffered profiles,
happy path, validated by contract/validate.py in both the JSONL and the JSON
document form.  Also asks the ADR-004 question directly: is what `open_run`
receives enough to write a conforming `TraceHeader`?
"""

from __future__ import annotations

import json

from vitruvyan_motus import DurabilityProfile, TRACE_SCHEMA_VERSION

from x4c_common import (
    OUT, LINEAR_SPEC_PATH, JsonlFileSink, Report, kinds_of_file, runtime,
    validate_file, write_json,
)


def main() -> int:
    r = Report("x4c-01 baseline: real file sink, healthy runs")

    # --- synchronous ------------------------------------------------------ #
    sink = JsonlFileSink(OUT / "01-sync", prefix="sync")
    rt = runtime(durability_profile=DurabilityProfile.SYNCHRONOUS, sink=sink)
    result = rt.run()
    path = sink.paths[0]
    code, out = validate_file(path, "jsonl", spec_path=LINEAR_SPEC_PATH)
    r.record(
        "sync/jsonl-valid", code == 0,
        f"status={result.status} records={len(result.trace.records)} "
        f"file={path.name} rc={code} {out[:200]}",
    )
    r.record(
        "sync/file-matches-trace",
        kinds_of_file(path)[1:] == [x["kind"] for x in result.trace.records],
        f"{kinds_of_file(path)}",
    )

    # the same run as a JSON document
    doc_path = write_json("01-sync-doc.json", result.trace.to_dict())
    code, out = validate_file(doc_path, "trace", spec_path=LINEAR_SPEC_PATH)
    r.record("sync/json-doc-valid", code == 0, f"rc={code} {out[:200]}")

    # --- buffered --------------------------------------------------------- #
    sink2 = JsonlFileSink(OUT / "01-buf", prefix="buf")
    rt2 = runtime(
        durability_profile=DurabilityProfile.BUFFERED, sink=sink2,
        chunk_records=3, flush_interval_ms=10_000,
    )
    result2 = rt2.run()
    path2 = sink2.paths[0]
    code, out = validate_file(path2, "jsonl", spec_path=LINEAR_SPEC_PATH)
    r.record(
        "buffered/jsonl-valid", code == 0,
        f"status={result2.status} batches={sink2.sessions[0].batches} rc={code} {out[:200]}",
    )
    r.record(
        "buffered/file-matches-trace",
        kinds_of_file(path2)[1:] == [x["kind"] for x in result2.trace.records],
        f"{len(kinds_of_file(path2)) - 1} records persisted, "
        f"{len(result2.trace.records)} in trace",
    )

    # --- in-memory with an explicitly attached sink ------------------------ #
    sink3 = JsonlFileSink(OUT / "01-mem", prefix="mem")
    rt3 = runtime(durability_profile=DurabilityProfile.IN_MEMORY, sink=sink3)
    result3 = rt3.run()
    code, out = validate_file(sink3.paths[0], "jsonl", spec_path=LINEAR_SPEC_PATH)
    r.record("in-memory+sink/jsonl-valid", code == 0, f"rc={code} {out[:200]}")

    # --- ADR-004 consequence 1: is the header self-sufficient? ------------- #
    header = sink.headers[0]
    r.record(
        "adr004/header-carries-schema_version",
        "schema_version" in header,
        f"open_run header keys = {sorted(header)}",
    )
    # Prove the consequence: writing line 1 from the header alone is invalid.
    naive = OUT / "01-naive-header.jsonl"
    lines = [json.dumps({"run": header}, separators=(",", ":"))]
    lines += [
        json.dumps(rec, separators=(",", ":")) for rec in result.trace.records
    ]
    naive.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    code, out = validate_file(naive, "jsonl")
    r.record(
        "adr004/protocol-alone-suffices", code == 0,
        f"a sink that writes exactly what open_run gave it: rc={code} :: {out[:240]}",
    )
    print(f"\n(package TRACE_SCHEMA_VERSION={TRACE_SCHEMA_VERSION}; the sink had "
          f"to import it — the protocol never delivered it.)")

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
