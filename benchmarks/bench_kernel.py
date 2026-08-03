"""Benchmark of the axis v0.4.0 graph kernel (tree: feat/order-spec, timing-equivalent).

Read-only w.r.t. the repo. Outputs a single JSON document to stdout.
Methodology, normative per contract/guarantees.md §3: time.perf_counter,
WARMUPS (2) discarded before >= 7 measured samples per measurement,
gc.collect() before every sample, report min and median.
"""
import gc
import json
import os
import statistics
import sys
from time import perf_counter

from axis import GraphState, Runner, Policy
from axis.state import Fact
from axis.events import now

REPEATS = 7
WARMUPS = 2
OUT = {}


def timeit(fn, repeats=REPEATS, warmups=WARMUPS):
    """Return (min_s, median_s, all_s) of `repeats` MEASURED calls to fn().

    The first `warmups` calls are executed and discarded — cold import paths,
    first-touch allocation and the interpreter's own caches otherwise land in
    sample 1 and skew the min.  guarantees.md §3 makes the warmup normative;
    the baseline JSON in this directory was regenerated with it.
    """
    for _ in range(warmups):
        gc.collect()
        fn()
    samples = []
    for _ in range(repeats):
        gc.collect()
        t0 = perf_counter()
        fn()
        samples.append(perf_counter() - t0)
    return min(samples), statistics.median(samples), samples


def identity(state):
    return state


def make_fact_node(i):
    def fact_node(state):
        return state.with_fact(Fact(f"k{i}", i, "bench", now()))
    return fact_node


class NoopObserver:
    critical = False

    def observe(self, event_type, state, **kwargs):
        pass


class MarkObserver:
    """Records perf_counter at graph_start and at every node_completed."""
    critical = False

    def __init__(self):
        self.marks = []

    def observe(self, event_type, state, **kwargs):
        if event_type in ("graph_start", "node_completed"):
            self.marks.append(perf_counter())


# ---------------------------------------------------------------- 1. BASELINE
baseline = {}
for n in (100, 1000):
    fns = [identity] * n

    def run_baseline(fns=fns):
        s = object()
        for f in fns:
            s = f(s)
        return s

    mn, md, _ = timeit(run_baseline)
    baseline[n] = {"min_us": mn * 1e6, "median_us": md * 1e6}
OUT["1_baseline"] = baseline

# ------------------------------------------------------------ 2. RUNNER no-op
runner_noop = {}
final_noop_state = {}
for n in (100, 1000):
    runner = Runner([identity] * n, policy=Policy.STRICT)
    keep = {}

    def run_it(runner=runner, keep=keep):
        keep["state"] = runner.run(GraphState.new("bench"))

    mn, md, _ = timeit(run_it)
    final_noop_state[n] = keep["state"]
    runner_noop[n] = {
        "min_ms": mn * 1e3,
        "median_ms": md * 1e3,
        "overhead_us_per_node_min": (mn - baseline[n]["min_us"] / 1e6) / n * 1e6,
        "overhead_us_per_node_median": (md - baseline[n]["median_us"] / 1e6) / n * 1e6,
    }
OUT["2_runner_noop"] = runner_noop

# -------------------------------------------------------- 3. RUNNER realistic
runner_real = {}
final_real_state = {}
for n in (100, 1000):
    nodes = [make_fact_node(i) for i in range(n)]
    runner = Runner(nodes, policy=Policy.STRICT)
    keep = {}

    def run_it(runner=runner, keep=keep):
        keep["state"] = runner.run(GraphState.new("bench"))

    mn, md, _ = timeit(run_it)
    final_real_state[n] = keep["state"]
    runner_real[n] = {
        "min_ms": mn * 1e3,
        "median_ms": md * 1e3,
        "us_per_node_min": mn / n * 1e6,
        "us_per_node_median": md / n * 1e6,
    }
OUT["3_runner_realistic"] = runner_real

# ---------------------------------------------------------- 4. SCALING CHECK
# (a) per-node cost windows inside single 1000-node realistic runs
windows = {"nodes_1_100": (0, 100), "nodes_401_500": (400, 500), "nodes_901_1000": (900, 1000)}
window_samples = {k: [] for k in windows}
for _ in range(REPEATS + WARMUPS):
    nodes = [make_fact_node(i) for i in range(1000)]
    runner = Runner(nodes, policy=Policy.STRICT)
    obs = MarkObserver()
    runner.attach(obs)
    gc.collect()
    runner.run(GraphState.new("bench"))
    marks = obs.marks  # [graph_start, completed_1 ... completed_1000]
    deltas = [marks[i + 1] - marks[i] for i in range(len(marks) - 1)]  # per-node cost
    for key, (a, b) in windows.items():
        window_samples[key].append(sum(deltas[a:b]) / (b - a))
scaling = {"per_node_us_windows": {}}
for key in windows:
    s = window_samples[key][WARMUPS:]  # discard the warmup runs (guarantees.md §3)
    scaling["per_node_us_windows"][key] = {
        "min_us": min(s) * 1e6,
        "median_us": statistics.median(s) * 1e6,
    }
w1 = scaling["per_node_us_windows"]["nodes_1_100"]["median_us"]
w5 = scaling["per_node_us_windows"]["nodes_401_500"]["median_us"]
w10 = scaling["per_node_us_windows"]["nodes_901_1000"]["median_us"]
scaling["growth_ratio_w10_over_w1"] = w10 / w1
scaling["linear_check_w5_expected_if_linear"] = (w1 + w10) / 2
scaling["t1000_vs_10x_t100_realistic"] = runner_real[1000]["min_ms"] / (10 * runner_real[100]["min_ms"])

# (b) direct with_event cost vs events-tuple size (the O(n) append itself)
with_event_cost = {}
ev = None
sizes = [0, 250, 500, 1000, 1500, 2000]
from axis.events import Event, EventType
probe_event = Event(event_type=EventType.NODE_COMPLETED, description="p", timestamp=now())
st = GraphState.new("scale")
next_size = 0
for size in sizes:
    while len(st.events) < size:
        st = st.with_event(probe_event)
    CALLS = 2000

    def bump(st=st):
        for _ in range(CALLS):
            st.with_event(probe_event)

    mn, md, _ = timeit(bump, repeats=5)
    with_event_cost[size] = {"per_call_us_min": mn / CALLS * 1e6,
                             "per_call_us_median": md / CALLS * 1e6}
scaling["with_event_us_by_events_len"] = with_event_cost
# slope: ns per existing event in the tuple
lo = with_event_cost[0]["per_call_us_min"]
hi = with_event_cost[2000]["per_call_us_min"]
scaling["with_event_slope_ns_per_existing_event"] = (hi - lo) / 2000 * 1e3
scaling["with_event_fixed_cost_us"] = lo
OUT["4_scaling"] = scaling

# -------------------------------------------------------------- 5. OBSERVERS
observers = {}
for n in (100, 1000):
    for n_obs in (1, 5):
        runner = Runner([identity] * n, policy=Policy.STRICT)
        for _ in range(n_obs):
            runner.attach(NoopObserver())

        def run_it(runner=runner):
            runner.run(GraphState.new("bench"))

        mn, md, _ = timeit(run_it)
        n_events = 2 * n + 2  # notify calls per run
        base_mn = runner_noop[n]["min_ms"] / 1e3
        base_md = runner_noop[n]["median_ms"] / 1e3
        observers[f"n{n}_obs{n_obs}"] = {
            "min_ms": mn * 1e3,
            "median_ms": md * 1e3,
            "marginal_us_per_observer_per_event_min": (mn - base_mn) / (n_obs * n_events) * 1e6,
            "marginal_us_per_observer_per_event_median": (md - base_md) / (n_obs * n_events) * 1e6,
        }
OUT["5_observers"] = observers

# ---------------------------------------------------------- 6. SERIALIZATION
serialization = {}
for label, st1000, run_ms in (
    ("noop_1000", final_noop_state[1000], runner_noop[1000]["min_ms"]),
    ("realistic_1000", final_real_state[1000], runner_real[1000]["min_ms"]),
):
    mn_d, md_d, _ = timeit(lambda s=st1000: s.to_dict())
    d = st1000.to_dict()
    mn_j, md_j, _ = timeit(lambda d=d: json.dumps(d))
    serialization[label] = {
        "events_len": len(st1000.events),
        "facts_len": len(st1000.facts),
        "to_dict_min_ms": mn_d * 1e3,
        "to_dict_median_ms": md_d * 1e3,
        "json_dumps_min_ms": mn_j * 1e3,
        "json_dumps_median_ms": md_j * 1e3,
        "to_dict_pct_of_run": mn_d * 1e3 / run_ms * 100,
        "json_dumps_pct_of_run": mn_j * 1e3 / run_ms * 100,
    }
OUT["6_serialization"] = serialization

# ------------------------------------------------------------- 7. ALLOCATION
allocation = {}
DERIVS = 10_000
one_fact = Fact("k", 1, "bench", now())
small = GraphState.new("alloc")           # 0 events, 0 facts
big_events = GraphState.new("alloc2")
while len(big_events.events) < 2000:
    big_events = big_events.with_event(probe_event)   # 2000 events, 0 facts
big_facts = GraphState.new("alloc3")
for _ in range(2000):
    big_facts = big_facts.with_fact(one_fact)          # 0 events, 2000 facts
for label, st in (("small_state", small),
                  ("state_with_2000_events", big_events),
                  ("state_with_2000_facts_extra", big_facts)):
    def derive(st=st):
        wf = st.with_fact
        for _ in range(DERIVS):
            wf(one_fact)

    mn, md, _ = timeit(derive)
    allocation[label] = {
        "total_ms_min": mn * 1e3,
        "total_ms_median": md * 1e3,
        "per_derivation_us_min": mn / DERIVS * 1e6,
        "per_derivation_us_median": md / DERIVS * 1e6,
    }
OUT["7_allocation"] = allocation

# ---------------------------------------------------------- 8. MICRO-PROFILE
import cProfile
import pstats
import io
nodes = [make_fact_node(i) for i in range(1000)]
runner = Runner(nodes, policy=Policy.STRICT)
state0 = GraphState.new("prof")
pr = cProfile.Profile()
pr.enable()
runner.run(state0)
pr.disable()
sio = io.StringIO()
ps = pstats.Stats(pr, stream=sio).sort_stats("cumulative")
total_tt = ps.total_tt
rows = []
for func, (cc, nc, tt, ct, callers) in sorted(
        ps.stats.items(), key=lambda kv: kv[1][3], reverse=True)[:14]:
    fname, lineno, name = func
    rows.append({
        "func": f"{os.path.basename(fname)}:{lineno}({name})",
        "ncalls": nc,
        "tottime_ms": tt * 1e3,
        "cumtime_ms": ct * 1e3,
        "pct_cum": ct / total_tt * 100,
    })
OUT["8_profile"] = {"total_profiled_ms": total_tt * 1e3, "top": rows}

# ------------------------------------------------------------------- env info
OUT["env"] = {
    "python": sys.version.split()[0],
    "loadavg": os.getloadavg(),
    "gc_enabled_during_runs": gc.isenabled(),
    "repeats": REPEATS,
}

print(json.dumps(OUT, indent=1))
