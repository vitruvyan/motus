"""Motus 0.5 benchmark using the guarantees.md section 3 method.

It intentionally emits the field shape consumed by ``check_slo_baseline.py``
without pretending that Motus trace records are Axis events.  In particular,
``events_len`` below is the complete native record count.
"""

from __future__ import annotations

import gc
import json
import os
import platform
import statistics
import sys
from datetime import datetime, timezone
from time import perf_counter

from vitruvyan_motus import Fact, GraphSpec, Runtime, State, __version__

REPEATS = 7
WARMUPS = 2
NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def cpu_model() -> str:
    cpuinfo = "/proc/cpuinfo"
    if os.path.exists(cpuinfo):
        with open(cpuinfo, encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine()


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
    return min(samples), statistics.median(samples), samples


def identity(state):
    return state


class AppendFactNode:
    __slots__ = ("key",)

    def __init__(self, key: str) -> None:
        self.key = key

    def motus_config(self):
        return {"key": self.key}

    def __call__(self, state):
        return state.with_fact(Fact(self.key, 1, "bench", NOW))


def make_runtime(size: int, *, realistic: bool = False):
    names = [f"n{index}" for index in range(size)]
    document = {
        "schema_version": "1.0.0",
        "name": f"motus-bench-{size}",
        "version": "0.5.0",
        "entry": names[0],
        "nodes": [
            {
                "name": name,
                "effect_class": "pure",
                **({"writes_declared": [name]} if realistic else {}),
            }
            for name in names
        ],
        "transitions": {
            name: (
                {"kind": "terminal"}
                if index == size - 1
                else {"kind": "next", "to": names[index + 1]}
            )
            for index, name in enumerate(names)
        },
    }
    registry = (
        {name: AppendFactNode(name) for name in names}
        if realistic
        else {name: identity for name in names}
    )
    return Runtime(GraphSpec.from_dict(document), registry)


out = {}
bare = {}
noop = {}
realistic = {}
final_noop = {}
final_realistic = {}

for size in (100, 1000):
    functions = [identity] * size

    def bare_run(functions=functions):
        value = object()
        for function in functions:
            value = function(value)
        return value

    minimum, median, _ = timeit(bare_run)
    bare[size] = {"min_us": minimum * 1e6, "median_us": median * 1e6}

    noop_runtime = make_runtime(size)
    noop_counter = [0]

    def noop_run(runtime=noop_runtime, counter=noop_counter, size=size):
        counter[0] += 1
        final_noop[size] = runtime.run(State.empty(), run_id=f"noop-{size}-{counter[0]}")

    minimum, median, _ = timeit(noop_run)
    noop[size] = {
        "min_ms": minimum * 1e3,
        "median_ms": median * 1e3,
        "overhead_us_per_node_min": (minimum - bare[size]["min_us"] / 1e6) / size * 1e6,
        "overhead_us_per_node_median": (median - bare[size]["median_us"] / 1e6) / size * 1e6,
    }

    real_runtime = make_runtime(size, realistic=True)
    real_counter = [0]

    def real_run(runtime=real_runtime, counter=real_counter, size=size):
        counter[0] += 1
        final_realistic[size] = runtime.run(State.empty(), run_id=f"real-{size}-{counter[0]}")

    minimum, median, _ = timeit(real_run)
    realistic[size] = {
        "min_ms": minimum * 1e3,
        "median_ms": median * 1e3,
        "us_per_node_min": minimum / size * 1e6,
        "us_per_node_median": median / size * 1e6,
    }

out["1_baseline"] = bare
out["2_runner_noop"] = noop
out["3_runner_realistic"] = realistic
ratio = realistic[1000]["min_ms"] / (10 * realistic[100]["min_ms"])
out["4_scaling"] = {
    "growth_ratio_w10_over_w1": ratio,
    "t1000_vs_10x_t100_realistic": ratio,
}

serialization = {}
for label, result in (
    ("noop_1000", final_noop[1000]),
    ("realistic_1000", final_realistic[1000]),
):
    minimum_dict, median_dict, _ = timeit(result.trace.to_dict)
    document = result.trace.to_dict()
    minimum_json, median_json, _ = timeit(
        lambda document=document: json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    )
    serialization[label] = {
        "events_len": len(result.trace.records),
        "facts_len": len(result.state.facts),
        "violations_len": sum(
            len(record.get("violations", ())) for record in result.trace.records
        ),
        "to_dict_min_ms": minimum_dict * 1e3,
        "to_dict_median_ms": median_dict * 1e3,
        "json_dumps_min_ms": minimum_json * 1e3,
        "json_dumps_median_ms": median_json * 1e3,
    }
out["6_serialization"] = serialization
out["env"] = {
    "runtime": f"vitruvyan-motus/{__version__}",
    "python": sys.version.split()[0],
    "cpu_model": cpu_model(),
    "platform_system": platform.system(),
    "platform_release": platform.release(),
    "machine": platform.machine(),
    "loadavg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    "gc_enabled_during_runs": gc.isenabled(),
    "repeats": REPEATS,
}

print(json.dumps(out, indent=1))
