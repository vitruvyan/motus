"""x3c_03 — the version bump broke the performance characterization loop.

`benchmarks/bench_motus.py:187` stamps every candidate document with
``f"vitruvyan-motus/{__version__}"``. `benchmarks/check_slo_baseline.py:151`
requires that string to equal the literal ``"vitruvyan-motus/0.6.1"``.

So `.github/workflows/motus-characterize.yml`, which regenerates the candidate,
now produces evidence the release gate refuses. CI stays green only because
the `slo-baseline` job validates the COMMITTED 0.6.1 document, which nobody
regenerated.

This proves it three ways: by reading what the collector stamps, by running
the real collector, and by putting the produced document through the gate.

Run: python .attack/x3c_03_version_coupling.py
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import vitruvyan_motus as M  # noqa: E402
from benchmarks.check_slo_baseline import (  # noqa: E402
    DEFAULT_BASELINE,
    DEFAULT_CANDIDATE,
    DEFAULT_GUARANTEES,
    GateError,
    load_document,
    run_gate,
    validate_document,
)


def main() -> int:
    print(f"installed __version__            : {M.__version__}")
    print(f"string bench_motus.py will stamp : vitruvyan-motus/{M.__version__}")
    gate_line = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-n", "vitruvyan-motus/0.6.1", "--",
         "benchmarks/check_slo_baseline.py"],
        capture_output=True, text=True).stdout.strip()
    print(f"what the gate requires           : {gate_line}")

    # 1. the committed candidate still passes (this is what CI runs)
    print("\n1. the COMMITTED 0.6.1 candidate through the gate:")
    try:
        results = dict(run_gate(DEFAULT_BASELINE, DEFAULT_GUARANTEES,
                                DEFAULT_CANDIDATE))
        print(f"   PASS — {sum(1 for k in results if k.startswith('Candidate'))} "
              f"candidate rows validated")
    except GateError as exc:
        print(f"   FAIL — {exc}")

    # 2. the same document, restamped with the version this tree produces
    print("\n2. the same document restamped with the CURRENT version:")
    document = copy.deepcopy(load_document(DEFAULT_CANDIDATE))
    stamp = f"vitruvyan-motus/{M.__version__}"
    document["env"]["runtime"] = stamp
    for run in document["runs"]:
        run["env"]["runtime"] = stamp
    try:
        validate_document(document, name="restamped", runtime_kind="motus")
        print(f"   accepted with runtime={stamp!r}")
    except GateError as exc:
        print(f"   REJECTED — {exc}")

    # 3. run the real collector and put its output through the gate
    print("\n3. running benchmarks/collect_motus_baseline.py for real "
          "(5 runs, ~1-2 min)...")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "benchmarks" / "collect_motus_baseline.py"), "5"],
        capture_output=True, text=True, cwd=str(ROOT),
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
    )
    if proc.returncode != 0:
        print(f"   collector failed: {proc.stderr[-800:]}")
        return 2
    produced = json.loads(proc.stdout)
    print(f"   collector produced env.runtime = "
          f"{produced.get('env', {}).get('runtime')!r}")
    out = Path("/tmp/claude-1000/-home-vitruvyan-motus/"
               "2eec55a9-be04-4965-b2c4-951aee158e49/scratchpad/x3c_candidate.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(produced))
    try:
        run_gate(DEFAULT_BASELINE, DEFAULT_GUARANTEES, out)
        print("   the freshly characterized document PASSES the gate")
    except GateError as exc:
        print(f"   the freshly characterized document is REJECTED: {exc}")
        print("   => .github/workflows/motus-characterize.yml can no longer "
              "produce evidence that .github/workflows/ci.yml will accept")

    # 4. does any test notice?
    print("\n4. does the suite notice?")
    hits = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-n", "vitruvyan-motus/", "--", "tests/"],
        capture_output=True, text=True).stdout.strip()
    print(f"   tests referencing the runtime identity:\n     "
          + "\n     ".join(hits.splitlines()))
    print("   (a hardcoded literal in the test keeps it green while the real "
          "pipeline breaks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
