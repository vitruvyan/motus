"""Attacks G/I/J/L: viewer output, compat vs native, race window, API surface."""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone

from vitruvyan_motus import (
    Fact, GraphSpec, Runtime, State, TraceBundle,
)

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
DOC = {
    "schema_version": "1.0.0", "name": "m", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
}
SPEC = GraphSpec.from_dict(DOC)
ident = lambda s: s

print("=== L1: hostile content in the standalone HTML viewer ===")
hostile_id = '</title><script>alert(1)</script>"onload="x'


def node(state):
    return state.with_fact(Fact("k", "<img src=x onerror=alert(2)>", "s\"'<>", NOW))


out = Runtime(SPEC, {"a": node}).run(State.empty("<b>intent</b>"), run_id=hostile_id)
html = TraceBundle(SPEC, out.trace).to_html()
print("   raw <script> in output      :", "<script>" in html)
print("   raw onerror= in output      :", "onerror=" in html)
print("   escaped &lt;script&gt; found:", "&lt;script&gt;" in html)
print("   remote asset references     :", any(t in html for t in ("http://", "https://", "//cdn")))

print("\n=== L2: explanation determinism ===")
b = TraceBundle(SPEC, out.trace)
import json  # noqa: E402
first = json.dumps(b.explain(), sort_keys=True)
second = json.dumps(TraceBundle(SPEC, out.trace).explain(), sort_keys=True)
print("   explain() stable across bundles:", first == second)
print("   bundle fingerprint stable      :", b.fingerprint == TraceBundle(SPEC, out.trace).fingerprint)

print("\n=== L3: 'no LLM/db/agent-framework import' fence ===")
import vitruvyan_motus, importlib, pkgutil  # noqa: E402
mods = [m.name for m in pkgutil.iter_modules(vitruvyan_motus.__path__)]
banned = {"axis", "langchain", "openai", "anthropic", "requests", "sqlalchemy", "psycopg2", "jsonschema", "numpy", "pydantic"}
loaded = set()
for name in mods:
    importlib.import_module(f"vitruvyan_motus.{name}")
for name in list(sys.modules):
    root = name.split(".")[0]
    if root in banned:
        loaded.add(root)
print("   submodules      :", sorted(mods))
print("   banned imported :", sorted(loaded) or "none")

print("\n=== L4: compat surface does not leak a second 'Decision' ===")
import vitruvyan_motus.compat as compat  # noqa: E402
print("   'Decision' in vitruvyan_motus.__all__       :", "Decision" in vitruvyan_motus.__all__)
print("   'Decision' in compat.__all__                :", "Decision" in compat.__all__)
print("   'LegacyDecision' in vitruvyan_motus dir()   :", "LegacyDecision" in dir(vitruvyan_motus))
print("   native Decision is compat.LegacyDecision    :",
      vitruvyan_motus.Decision is compat.LegacyDecision)
print("   compat Fact is native Fact                  :", compat.Fact is vitruvyan_motus.Fact)
missing = [n for n in vitruvyan_motus.__all__ if not hasattr(vitruvyan_motus, n)]
print("   __all__ names that do not resolve           :", missing or "none")

print("\n=== I6: the overlapping-run guard is a non-atomic check-then-set ===")
# Deterministically interleave the two threads inside Runtime._start by
# tracing the exact source line of the guard.
import vitruvyan_motus.runtime as runtime_mod  # noqa: E402
source = open(runtime_mod.__file__, encoding="utf-8").read().splitlines()
guard_line = next(i + 1 for i, line in enumerate(source)
                  if line.strip() == "if self._running:")
print("   guard at runtime.py line", guard_line)

rt = Runtime(SPEC, {"a": ident})
barrier = threading.Barrier(2, timeout=5)
hit = threading.Event()
box = []


def tracer(frame, event, arg):
    if event == "line" and frame.f_code.co_filename == runtime_mod.__file__ \
       and frame.f_lineno == guard_line and not hit.is_set():
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
    return tracer


def go():
    threading.settrace(tracer)
    sys.settrace(tracer)
    try:
        result = rt.run(State.empty("race"))
        box.append(("ok", [r["kind"] for r in result.trace.records],
                    [r["seq"] for r in result.trace.records]))
    except RuntimeError as exc:
        box.append(("guard", str(exc), None))
    except BaseException as exc:  # noqa: BLE001
        box.append((type(exc).__name__, str(exc), None))
    finally:
        sys.settrace(None)
        threading.settrace(None)


threads = [threading.Thread(target=go) for _ in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print("   outcomes:", [b[0] for b in box])
for tag, kinds, seqs in box:
    if tag == "ok":
        ok = kinds[0] == "run_started" and kinds[-1] == "run_completed" \
            and seqs == list(range(1, len(seqs) + 1)) and kinds.count("run_started") == 1
        print(f"     trace: {kinds} seqs={seqs} coherent={ok}")
    else:
        print(f"     {tag}: {kinds}")
print("   runtime._running after both threads:", rt._running)

print("\n=== G1: pure node effect enforcement is double-gated ===")
from vitruvyan_motus import EffectClass, EffectDescriptor  # noqa: E402
from vitruvyan_motus.context import _RunController  # noqa: E402
control = _RunController()
control.begin_effect_scope("recorded_effect")
try:
    control._record_effect(EffectDescriptor(EffectClass.EXTERNAL_EFFECT, "x"))
    print("   recorded_effect scope accepted an external effect  <-- unexpected")
except ValueError as exc:
    print("   recorded_effect scope refuses external effect:", exc)
