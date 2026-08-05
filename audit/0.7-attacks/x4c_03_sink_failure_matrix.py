"""x4c-03 — Sink failure paths: does invariant II hold on every terminal?

guarantees.md invariant II: "A run cannot declare itself completed if its
required sink has not accepted the trace."  §6 TraceSink/Failure: "the runner
does not silently drop and continue."  OPEN-08 licenses ONE thing: when the
failing component IS the sink, the run_failed(sink_failure) record's own
persistence is best-effort — "the run's logical failure toward the caller is
still guaranteed".

So for every terminal shape, this asks two separate questions:

  (a) did the CALLER learn that the required sink refused evidence?
  (b) what is left in the file, and does it validate?

A row where the caller learned nothing is a swallowed sink failure.
"""

from __future__ import annotations

import json
import traceback

from vitruvyan_motus import (
    Decision, DurabilityProfile, GraphSpec, NodeFailed, Runtime, SinkFailed, State,
)

from x4c_common import (
    LINEAR, LINEAR_SPEC_PATH, OUT, JsonlFileSink, fail_on_kind, kinds_of_file,
    registry, validate_file, write_json,
)

ROWS: list[dict] = []

# A graph that route-misses under STRICT: `a` records no decision.
MISS_DOC = {
    "schema_version": "1.0.0", "name": "x4c-miss", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {
        "a": {"kind": "route", "on": "never_written", "map": {"x": "b"}},
        "b": {"kind": "terminal"},
    },
}
MISS = GraphSpec.from_dict(dict(MISS_DOC))
MISS_SPEC_PATH = write_json("x4c-miss.graphspec.json", MISS.to_dict())


def node(state: State) -> State:
    return state


def raiser(state: State) -> State:
    raise ValueError("node failed on purpose")


def row(name, *, caller_saw, status, sink, spec_path, note=""):
    path = sink.sessions[-1].path if sink.sessions else None
    if path is None:
        rc, out, kinds = None, "no file", []
    else:
        kinds = kinds_of_file(path)
        rc, out = validate_file(path, "jsonl", spec_path=spec_path)
    ROWS.append({
        "case": name,
        "caller_learned_of_sink_failure": caller_saw,
        "run_status": status,
        "persisted_records": max(len(kinds) - 1, 0),
        "persisted_last": kinds[-1] if len(kinds) > 1 else "(none)",
        "file_rc": rc,
        "file_verdict": (out.splitlines()[0][:110] if out else "VALID"),
        "note": note,
    })


def run_case(name, *, profile, fail, spec=LINEAR, spec_path=LINEAR_SPEC_PATH,
             nodes=None, policy="strict", pre_cancel=False, hub_kwargs=None,
             note=""):
    sink = JsonlFileSink(OUT / "03" / name.replace(" ", "_")[:40], prefix="s", fail=fail)
    kwargs = dict(durability_profile=profile, sink=sink, policy=policy)
    kwargs.update(hub_kwargs or {})
    rt = Runtime(spec, nodes or registry(), **kwargs)
    if pre_cancel:
        rt.cancel("cancelled before the first run")
    caller_saw, status = "NO", "?"
    try:
        result = rt.run()
        status = result.status
    except SinkFailed as exc:
        caller_saw, status = "SinkFailed", "raised"
        # The trace SinkFailed carries is the run's own evidence: check it.
        doc = write_json(f"03-{name.replace(' ', '_')[:40]}-inmem.json",
                         exc.trace.to_dict())
        rc, out = validate_file(doc, "trace", spec_path=spec_path)
        note = (note + f" | in-memory trace rc={rc} "
                       f"{(out.splitlines()[0][:80] if out else 'VALID')}").strip(" |")
    except NodeFailed as exc:
        caller_saw, status = "NodeFailed (node cause, NOT sink)", "raised"
    except BaseException as exc:  # noqa: BLE001
        caller_saw, status = f"{type(exc).__name__}", "raised"
    row(name, caller_saw=caller_saw, status=status, sink=sink,
        spec_path=spec_path, note=note)


def main() -> int:
    SYNC = DurabilityProfile.SYNCHRONOUS
    BUF = DurabilityProfile.BUFFERED

    # --- open_run refusal ------------------------------------------------- #
    for profile, tag in ((SYNC, "sync"), (BUF, "buffered")):
        sink = JsonlFileSink(OUT / "03" / f"open-refuse-{tag}", prefix="s",
                             fail_open=OSError("open_run refused"))
        rt = Runtime(LINEAR, registry(), durability_profile=profile, sink=sink)
        caller, status = "NO", "?"
        try:
            res = rt.run()
            status = res.status
        except SinkFailed:
            caller, status = "SinkFailed", "raised"
        except BaseException as exc:  # noqa: BLE001
            caller, status = type(exc).__name__, "raised"
        row(f"open_run refuses [{tag}] + run()", caller_saw=caller, status=status,
            sink=sink, spec_path=LINEAR_SPEC_PATH,
            note="ADR-004: refusal is a sink failure")

    # --- write refusal on each batch position ----------------------------- #
    for profile, tag in ((SYNC, "sync"), (BUF, "buffered")):
        hub = {"chunk_records": 1, "flush_interval_ms": 0} if profile is BUF else {}
        run_case(f"write refuses run_started [{tag}]", profile=profile,
                 fail=fail_on_kind("run_started"), hub_kwargs=hub)
        run_case(f"write refuses a mid transition [{tag}]", profile=profile,
                 fail=fail_on_kind("transition"), hub_kwargs=hub)
        run_case(f"write refuses run_completed [{tag}]", profile=profile,
                 fail=fail_on_kind("run_completed"), hub_kwargs=hub)
        # Terminal shapes that are NOT run_completed:
        run_case(f"write refuses run_cancelled [{tag}]", profile=profile,
                 fail=fail_on_kind("run_cancelled"), pre_cancel=True,
                 hub_kwargs=hub,
                 note="run ends cancelled; who learns the sink refused?")
        run_case(f"write refuses run_failed/route_miss [{tag}]", profile=profile,
                 fail=fail_on_kind("run_failed"), spec=MISS,
                 spec_path=MISS_SPEC_PATH,
                 nodes={"a": node, "b": node}, hub_kwargs=hub,
                 note="strict route miss returns a RunResult, never raises")
        nodes = dict(registry())
        nodes["b"] = raiser
        run_case(f"write refuses run_failed/node_failure [{tag}]", profile=profile,
                 fail=fail_on_kind("run_failed"), nodes=nodes, hub_kwargs=hub)

    print("\n=== x4c-03: sink failure vs. what the caller and the file learn ===")
    hdr = (f"{'case':<44} {'caller learned':<34} {'status':<10} "
           f"{'recs':>4} {'last':<15} {'rc':>3}")
    print(hdr)
    print("-" * len(hdr))
    for r in ROWS:
        print(
            f"{r['case']:<44} {r['caller_learned_of_sink_failure']:<34} "
            f"{r['run_status']:<10} {r['persisted_records']:>4} "
            f"{str(r['persisted_last']):<15} {str(r['file_rc']):>3}"
        )
    print("\nnotes:")
    for r in ROWS:
        if r["note"]:
            print(f"  {r['case']}: {r['note']}")
    silent = [r for r in ROWS if r["caller_learned_of_sink_failure"] == "NO"]
    print(f"\n-- {len(ROWS)} cases; {len(silent)} SWALLOWED the sink failure entirely")
    for r in silent:
        print(f"   SWALLOWED: {r['case']}  (status={r['run_status']}, "
              f"file rc={r['file_rc']}: {r['file_verdict']})")
    (OUT / "03-summary.json").write_text(json.dumps(ROWS, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
