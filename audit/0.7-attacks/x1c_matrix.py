"""ROUND 3, ITEM 7 -- equivalence on fb30b8d, and whether the narrowing is
SUFFICIENT.

The claim narrowed: replay constraints derived from node identity are registry
properties, like `code_fingerprint`, not driver properties. That splits the
comparison in two, and only one of them is the driver axis:

  (a) DRIVER AXIS -- the same registry driven by run() and by arun().
      Nothing whatsoever may differ. A registry property is identical to
      itself, so the narrowing buys this comparison nothing and it must stay
      byte-for-byte perfect.

  (b) REGISTRY AXIS -- a synchronous registry vs its async-def twin. Here the
      narrowing applies. The question is not "does something differ" but
      "does ONLY what was declared differ". This script enumerates every
      differing JSON path across all 27 graph shapes, normalises array
      indices, and reports the exact set — so an unaccounted field cannot
      hide behind an expected one.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRATCH = ("/tmp/claude-1000/-home-vitruvyan-motus/"
           "2eec55a9-be04-4965-b2c4-951aee158e49/scratchpad")

TREES = {
    "bb886e6 (pre-inversion)": f"{SCRATCH}/pre/src",
    "51e2439 (post-inversion)": f"{SCRATCH}/prefix/src",
    "a72cdf1 (round-2 fix)": f"{SCRATCH}/h2/src",
    "fb30b8d (round-3 fix)": f"{SCRATCH}/h3/src",
}

# What the narrowing declares may differ on the REGISTRY axis.
DECLARED = {
    "$.run.graph.code_fingerprint",
    "$.records[*].replay.capability",
    "$.records[*].replay.constraints",
    "$.records[*].replay.constraints[*]",
}

from x1_common import BEHAVIOURS, Report, async_registry, diff, doc, sync_registry  # noqa: E402
import x1_matrix as M  # noqa: E402

from vitruvyan_motus import State  # noqa: E402


def normalise(path: str) -> str:
    return re.sub(r"\[\d+\]", "[*]", path)


def dump_sync(src: str):
    proc = subprocess.run(
        [sys.executable, str(HERE / "x1_prediff.py"), "dump"],
        capture_output=True, text=True,
        env=dict(os.environ, MOTUS_SRC=src, PYTHONPATH=str(HERE)),
        cwd=str(HERE), timeout=900,
    )
    if proc.returncode != 0:
        raise SystemExit(f"dump failed for {src}:\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout)


async def main() -> int:
    report = Report("x1c_matrix -- equivalence on fb30b8d and the narrowing audit")

    # ================= four-way synchronous differential ==================
    missing = [l for l, s in TREES.items() if not Path(s).is_dir()]
    if missing:
        print(f"missing trees: {missing}")
        return 1
    sides = {label: dump_sync(src) for label, src in TREES.items()}
    for left, right in combinations(sides, 2):
        bad = []
        for name in sides[right]:
            d = diff(sides[left][name], sides[right][name], f"${name}")
            if d:
                bad.append(f"{name}: " + "; ".join(d[:3]))
        report.record(f"{left}  ==  {right}  (27 cases, sync path)",
                      not bad, " || ".join(bad[:3]))

    # ================= (a) the driver axis ================================
    driver_diffs: dict[str, list[str]] = {}
    registry_paths: dict[str, set[str]] = {}
    conformance_bad: list[str] = []

    for case_ in M.CASES:
        name = case_["name"]
        state_factory = case_["state"] or (lambda: State.empty(name))
        names = case_["names"]

        BEHAVIOURS.clear(); BEHAVIOURS.update(case_["behaviours"]())
        rt_s = M.build(case_, sync_registry(names))
        M.capture_sync(lambda: rt_s.run(state_factory(), run_id="pinned"))
        doc_s = doc(rt_s.trace)

        BEHAVIOURS.clear(); BEHAVIOURS.update(case_["behaviours"]())
        rt_a = M.build(case_, sync_registry(names))
        await M.capture_async(rt_a.arun(state_factory(), run_id="pinned"))
        doc_a = doc(rt_a.trace)

        d = diff(doc_s, doc_a)
        if d:
            driver_diffs[name] = d

        # ---- (b) the registry axis --------------------------------------
        BEHAVIOURS.clear(); BEHAVIOURS.update(case_["behaviours"]())
        rt_ab = M.build(case_, async_registry(names))
        await M.capture_async(rt_ab.arun(state_factory(), run_id="pinned"))
        doc_ab = doc(rt_ab.trace)
        paths = {normalise(entry.split(":")[0]) for entry in diff(doc_s, doc_ab)}
        registry_paths[name] = paths

        for label, document, trace in (("sync", doc_s, rt_s.trace),
                                       ("async", doc_a, rt_a.trace),
                                       ("asyncdef", doc_ab, rt_ab.trace)):
            spec_doc = dict(case_["spec"])
            j = M.validate.validate_trace(document, spec=spec_doc)
            lines, reassembled = M.validate.validate_jsonl(trace.to_jsonl(), spec=spec_doc)
            if j or lines or reassembled != document:
                conformance_bad.append(f"{name}/{label}: json={j[:1]} jsonl={lines[:1]}")

    report.record(
        "(a) DRIVER AXIS: run() == arun() byte-for-byte on the identical "
        "registry, all 27 shapes — including replay, which is a registry "
        "property and therefore identical to itself",
        not driver_diffs,
        "; ".join(f"{k}: {v[:2]}" for k, v in list(driver_diffs.items())[:3]),
    )

    observed = set().union(*registry_paths.values()) if registry_paths else set()
    unaccounted = observed - DECLARED
    report.record(
        "(b) REGISTRY AXIS: the sync/async-def difference is EXACTLY the "
        "declared set — code_fingerprint and the replay constraint — and "
        "nothing else",
        not unaccounted,
        f"unaccounted fields: {sorted(unaccounted)}",
    )
    print(f"    observed differing paths (registry axis): {sorted(observed)}")
    print(f"    declared allowed:                        {sorted(DECLARED)}")

    report.record(
        "the DECLARED replay in the header (run.replay) is untouched by the "
        "constraint — only the terminal record's replay degrades",
        "$.run.replay.capability" not in observed
        and "$.run.replay.constraints" not in observed
        and "$.run.replay.constraints[*]" not in observed,
        f"observed={sorted(p for p in observed if p.startswith('$.run.replay'))}",
    )
    report.record(
        "every trace from every shape and every driver is contract-valid in "
        "both encodings and reassembles identically",
        not conformance_bad, "; ".join(conformance_bad[:3]),
    )

    # which shapes actually earn a constraint
    earned = sorted(n for n, p in registry_paths.items()
                    if any("replay" in x for x in p))
    print(f"    shapes whose async twin earns a replay constraint: "
          f"{len(earned)}/{len(registry_paths)}")
    return report.emit()


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
