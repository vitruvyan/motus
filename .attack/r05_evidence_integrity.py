"""Re-audit Phase 6/7: recompute the published performance evidence, and
measure what the remediation actually costs per run."""

from __future__ import annotations

import gc
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]

print("=== P1: recompute every published aggregate from the raw runs ===")
doc = json.loads((ROOT / "benchmarks" / "candidate-v0.6.1-epyc-py310.json").read_text())
runs = doc["runs"]
print(f"   runs collected: {len(runs)} (>=5 required: {len(runs) >= 5})")
print(f"   kind={doc.get('kind')!r} runs_collected={doc.get('runs_collected')}")


def across(path):
    values = []
    for run in runs:
        node = run
        for key in path:
            node = node[key]
        values.append(node)
    return values


def spread(values):
    return (max(values) - min(values)) / min(values) * 100.0


metrics = {
    "per_node_us": across(("3_runner_realistic", "1000", "us_per_node_min")),
    "noop_100_ms": [r["2_runner_noop"]["100"]["overhead_us_per_node_min"] * 100 / 1000
                    for r in runs],
    "to_dict_ms": across(("6_serialization", "realistic_1000", "to_dict_min_ms")),
    "json_ms": across(("6_serialization", "realistic_1000", "json_dumps_min_ms")),
    "scaling": across(("4_scaling", "growth_ratio_w10_over_w1")),
}
for label, values in metrics.items():
    print(f"   {label:14} median={statistics.median(values):10.5f} spread={spread(values):6.2f}%")

ratio = statistics.median(metrics["to_dict_ms"]) / statistics.median(metrics["json_ms"])
scaling = statistics.median(metrics["scaling"])
superlinear = max(0.0, (scaling - 1.0) / scaling * 100.0)
print(f"   derived cold-materialization ratio : {ratio:.4f}x")
print(f"   derived positive superlinearity    : {superlinear:.4f}% (ratio {scaling:.5f})")

print("\n   ADR-007 claims vs recomputation:")
claims = {
    "per-node us": (51.2301, statistics.median(metrics["per_node_us"])),
    "noop ms": (3.43542, statistics.median(metrics["noop_100_ms"])),
    "cold ratio x": (1.749, ratio),
    "superlinear %": (3.47, superlinear),
}
for label, (claimed, computed) in claims.items():
    agree = abs(claimed - computed) < max(0.01, abs(claimed) * 0.01)
    print(f"   {label:16} ADR={claimed:<10} recomputed={computed:<10.5f} agree={agree}")

print("\n   per-run completeness:")
for index, run in enumerate(runs):
    s = run["6_serialization"]
    print(f"      run {index}: realistic events={s['realistic_1000']['events_len']} "
          f"facts={s['realistic_1000']['facts_len']} "
          f"violations={s['realistic_1000']['violations_len']} "
          f"| cached_to_json={s['realistic_1000'].get('cached_to_json_min_ms', '<absent>')}")

print("\n   environment identity of every run:")
for index, run in enumerate(runs):
    env = run["env"]
    print(f"      run {index}: {env['runtime']} py{env['python']} "
          f"cpu={env['cpu_model'][:28]!r} gc={env['gc_enabled_during_runs']} "
          f"repeats={env['repeats']}")

print("\n=== P2: guarantees.md table vs the recomputed evidence ===")
markdown = (ROOT / "contract" / "guarantees.md").read_text()
section = markdown.split("### Motus 0.6.1 GitHub EPYC profile")[1].split("\n\n")[0]
for line in section.splitlines():
    if line.startswith("| Motus"):
        print("   " + line.strip())
print(f"   ceilings at +25%: per_node<=56.25  noop<=4.0625  ratio<=1.875  superlinear<12.5")
print(f"   measured        : {statistics.median(metrics['per_node_us']):.2f} / "
      f"{statistics.median(metrics['noop_100_ms']):.4f} / {ratio:.3f} / {superlinear:.2f}%")
print("   NOTE: three of four rows now exceed their TARGET but stay inside the ceiling.")

print("\n=== P3: is the cold ratio arithmetically possible? ===")
print("   to_dict() = json.loads(json.dumps(view)); a dumps+loads cannot be")
print("   faster than the dumps alone, so any ratio < 1.0 would be impossible.")
print(f"   0.6.0 published 0.788x (impossible -> cache hit); 0.6.1 recomputes {ratio:.3f}x.")

print("\n=== P4: what does the per-run identity refresh cost? ===")
sys.path.insert(0, str(ROOT / "src"))
from vitruvyan_motus import Fact, GraphSpec, Runtime, State  # noqa: E402

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


class AppendFactNode:
    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key

    def motus_config(self):
        return {"key": self.key}

    def __call__(self, state):
        return state.with_fact(Fact(self.key, 1, "bench", NOW))


class PlainNode:
    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key

    def __call__(self, state):
        return state.with_fact(Fact(self.key, 1, "bench", NOW))


def make(size, cls):
    names = [f"n{i}" for i in range(size)]
    document = {
        "schema_version": "1.0.0", "name": f"b-{size}", "version": "0.6.1",
        "entry": names[0],
        "nodes": [{"name": n, "effect_class": "pure", "writes_declared": [n]} for n in names],
        "transitions": {n: ({"kind": "terminal"} if i == size - 1
                            else {"kind": "next", "to": names[i + 1]})
                        for i, n in enumerate(names)},
    }
    return Runtime(GraphSpec.from_dict(document), {n: cls(n) for n in names})


def timeit(fn, repeats=7, warmups=2):
    for _ in range(warmups):
        gc.collect()
        fn()
    samples = []
    for _ in range(repeats):
        gc.collect()
        started = perf_counter()
        fn()
        samples.append(perf_counter() - started)
    return min(samples)


for label, cls in (("motus_config() node (the benchmark's)", AppendFactNode),
                   ("opaque node, no motus_config()", PlainNode)):
    runtime = make(1000, cls)
    counter = [0]

    def once(runtime=runtime, counter=counter):
        counter[0] += 1
        runtime.run(State.empty(), run_id=f"p4-{counter[0]}")

    total = timeit(once)
    refresh = timeit(runtime._refresh_identity)
    print(f"   {label:38} run={total*1e3:8.2f} ms  "
          f"identity_refresh={refresh*1e3:7.2f} ms  ({refresh/total*100:5.1f}% of the run)")

print("\n=== P5: keyed-read cost, the ADV-004 remediation, measured ===")


class ReadThenAppend:
    __slots__ = ("key", "probe")

    def __init__(self, key, probe="n0"):
        self.key = key
        self.probe = probe

    def motus_config(self):
        return {"key": self.key}

    def __call__(self, state):
        state.fact(self.probe)
        return state.with_fact(Fact(self.key, 1, "bench", NOW))


for size in (100, 250, 500, 1000):
    runtime = make(size, ReadThenAppend)
    counter = [0]

    def once(runtime=runtime, counter=counter, size=size):
        counter[0] += 1
        runtime.run(State.empty(), run_id=f"p5-{size}-{counter[0]}")

    total = timeit(once, repeats=5, warmups=1)
    print(f"   n={size:5} write+read {total/size*1e6:8.2f} us/node  total {total*1e3:8.2f} ms")
