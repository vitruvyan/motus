"""Attack K: performance and resource behaviour, using the documented method.

Two specific claims are challenged:

1. "0.788x trace preparation versus json.dumps" -- benchmarks/bench_motus.py
   times ``result.trace.to_dict`` seven times on ONE Trace object.  Trace
   caches ``to_json``; ``to_dict`` is ``json.loads(self.to_json())``.  After
   the first (warm-up) call every measured sample therefore times a C-level
   json.loads of a cached string, not the serialization it is compared with.

2. "no positive superlinear term in the measured profile" -- the benchmark's
   node only WRITES.  State reads scan the whole committed log per lookup
   (State._items), so the record-and-compare surface that the node protocol is
   built around is never exercised by the gate.
"""

from __future__ import annotations

import gc
import json
import statistics
import sys
import tracemalloc
from datetime import datetime, timezone
from time import perf_counter

from vitruvyan_motus import Fact, GraphSpec, Runtime, State

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
REPEATS, WARMUPS = 7, 2


def timeit(fn, repeats=REPEATS, warmups=WARMUPS):
    for _ in range(warmups):
        gc.collect()
        fn()
    samples = []
    for _ in range(repeats):
        gc.collect()
        started = perf_counter()
        fn()
        samples.append(perf_counter() - started)
    return min(samples), statistics.median(samples)


class AppendFactNode:
    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key

    def motus_config(self):
        return {"key": self.key}

    def __call__(self, state):
        return state.with_fact(Fact(self.key, 1, "bench", NOW))


class ReadThenAppendNode:
    """A node that does what node-protocol section 3 is built around."""

    __slots__ = ("key", "probe")

    def __init__(self, key, probe):
        self.key = key
        self.probe = probe

    def motus_config(self):
        return {"key": self.key}

    def __call__(self, state):
        state.fact(self.probe)
        return state.with_fact(Fact(self.key, 1, "bench", NOW))


def make_runtime(size, factory):
    names = [f"n{i}" for i in range(size)]
    document = {
        "schema_version": "1.0.0", "name": f"bench-{size}", "version": "0.6.0",
        "entry": names[0],
        "nodes": [{"name": n, "effect_class": "pure", "writes_declared": [n]} for n in names],
        "transitions": {n: ({"kind": "terminal"} if i == size - 1
                            else {"kind": "next", "to": names[i + 1]})
                        for i, n in enumerate(names)},
    }
    return Runtime(GraphSpec.from_dict(document), {n: factory(n, names) for n in names})


print("=== K1: to_dict() is cached — the published serialization ratio ===")
runtime = make_runtime(1000, lambda n, names: AppendFactNode(n))
result = runtime.run(State.empty(), run_id="k1")
print("   records:", len(result.trace.records), "| facts:", len(result.state.facts))

# Exactly what bench_motus.py does.
warm_dict_min, _ = timeit(result.trace.to_dict)
document = result.trace.to_dict()
warm_json_min, _ = timeit(lambda: json.dumps(document, ensure_ascii=False, separators=(",", ":")))
print(f"   bench_motus.py method   : to_dict={warm_dict_min*1e3:8.3f} ms  "
      f"json.dumps={warm_json_min*1e3:8.3f} ms  ratio={warm_dict_min/warm_json_min:.3f}x")

# The same measurement on a COLD trace each time (real persist preparation).
def cold_to_dict():
    fresh = runtime.run(State.empty(), run_id=f"k1-cold-{cold_counter[0]}")
    cold_counter[0] += 1
    return fresh


cold_counter = [0]
traces = [runtime.run(State.empty(), run_id=f"k1-pre-{i}").trace for i in range(WARMUPS + REPEATS)]
samples = []
for index, trace in enumerate(traces):
    gc.collect()
    started = perf_counter()
    trace.to_dict()
    elapsed = perf_counter() - started
    if index >= WARMUPS:
        samples.append(elapsed)
cold_dict_min = min(samples)
print(f"   cold (uncached) to_dict : to_dict={cold_dict_min*1e3:8.3f} ms  "
      f"json.dumps={warm_json_min*1e3:8.3f} ms  ratio={cold_dict_min/warm_json_min:.3f}x")

traces = [runtime.run(State.empty(), run_id=f"k1-j-{i}").trace for i in range(WARMUPS + REPEATS)]
samples = []
for index, trace in enumerate(traces):
    gc.collect()
    started = perf_counter()
    trace.to_json()
    elapsed = perf_counter() - started
    if index >= WARMUPS:
        samples.append(elapsed)
cold_json_min = min(samples)
print(f"   cold to_json (serialize): to_json={cold_json_min*1e3:8.3f} ms  "
      f"ratio vs json.dumps={cold_json_min/warm_json_min:.3f}x")

print("\n=== K2: per-node cost with and without a single state read ===")
for label, factory in (
    ("write only (the gate's node)", lambda n, names: AppendFactNode(n)),
    ("write + 1 read (protocol shape)", lambda n, names: ReadThenAppendNode(n, names[0])),
):
    row = []
    for size in (100, 250, 500, 1000):
        rt = make_runtime(size, factory)
        counter = [0]

        def once(rt=rt, counter=counter, size=size):
            counter[0] += 1
            rt.run(State.empty(), run_id=f"k2-{size}-{counter[0]}")

        minimum, _ = timeit(once, repeats=5, warmups=1)
        row.append((size, minimum / size * 1e6, minimum * 1e3))
    print(f"   {label}")
    for size, per_node, total in row:
        print(f"      n={size:5}  {per_node:8.2f} us/node   total {total:8.2f} ms")
    ratio = row[-1][2] / (10 * row[1][2])
    print(f"      superlinearity t1000/(10*t100)... n=1000 vs 10x n=250x4: "
          f"{row[-1][2] / (4 * row[1][2]):.3f} (1.0 = linear)")

print("\n=== K3: memory growth across many runs on one Runtime ===")
rt = make_runtime(100, lambda n, names: AppendFactNode(n))
gc.collect()
tracemalloc.start()
base = tracemalloc.get_traced_memory()[0]
for i in range(200):
    rt.run(State.empty(), run_id=f"k3-{i}")
gc.collect()
after = tracemalloc.get_traced_memory()[0]
tracemalloc.stop()
print(f"   200 x 100-node runs: retained delta = {(after - base)/1024:.1f} KiB "
      f"(one live trace is expected)")

print("\n=== K4: is the published record count reproduced? ===")
r = make_runtime(1000, lambda n, names: AppendFactNode(n)).run(State.empty(), run_id="k4")
violations = sum(len(x.get("violations", ())) for x in r.trace.records)
print(f"   records={len(r.trace.records)} facts={len(r.state.facts)} violations={violations}")
