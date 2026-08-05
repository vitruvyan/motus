"""x3c_02 — isolate the async constraint, then attack the API and the version.

PART 1. In x3c_01 every node was a closure, so `node:<n>:opaque_config`
degraded every terminal on its own and MASKED whether the new async
constraint does anything. These nodes are module-level plain functions, so a
sync graph keeps `full` and any degradation is attributable.

PART 2. `__all__` vs v0.6.1 and vs a72cdf1; every name resolves; the wheel;
what still hardcodes 0.6.1.

PART 3. `0.7.0.dev0` against PEP 440 and against `requires_motus` — including
whether the specifiers the contract's own fixtures use would match it.

Run: python .attack/x3c_02_isolation_api_version.py
"""

from __future__ import annotations

import asyncio
import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import vitruvyan_motus as M  # noqa: E402
from vitruvyan_motus import (  # noqa: E402
    Fact,
    GraphSpec,
    NodeFailed,
    ReplayEngine,
    ReplayStatus,
    Runtime,
    State,
    TraceBundle,
)

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)

LINEAR_DOC = {
    "schema_version": "1.0.0", "name": "x3c-iso", "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"},
              {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}
LINEAR = GraphSpec.from_dict(dict(LINEAR_DOC))


# ---- module-level nodes: no closure cells, so no opaque_config ------------- #

def sync_a(state: State) -> State:
    return state.with_fact(Fact("a", "done", "x3c", NOW))


def sync_b(state: State) -> State:
    return state.with_fact(Fact("b", "done", "x3c", NOW))


async def async_a(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("a", "done", "x3c", NOW))


async def _coroutine_body(state: State) -> State:
    await asyncio.sleep(0)
    return state.with_fact(Fact("a", "done", "x3c", NOW))


def plain_def_returning_coroutine(state: State):
    """A plain `def`. `inspect.iscoroutinefunction` is False for it, yet it
    hands back exactly what an `async def` node would."""
    return _coroutine_body(state)


async def async_generator_a(state: State):
    yield state.with_fact(Fact("a", "done", "x3c", NOW))


def status_of(trace) -> tuple[str, str]:
    doc = trace.to_dict()
    declared = doc["run"].get("replay") or {}
    terminals = [r for r in doc["records"]
                 if r["kind"] in ("run_completed", "run_failed", "run_cancelled")]
    final = (terminals[-1].get("replay") or {}) if terminals else {}

    def fmt(s):
        if not s:
            return "(no terminal)"
        return f"{s.get('capability')}[{','.join(s.get('constraints') or [])}]"
    return fmt(declared), fmt(final)


def part1() -> None:
    print("=== PART 1 — is the async constraint visible on its own? ===")
    rows = []

    result = Runtime(LINEAR, {"a": sync_a, "b": sync_b}).run(
        State.empty("x"), replay=ReplayStatus.declared("full"))
    rows.append(("control: two module-level sync nodes", *status_of(result.trace)))

    trace = asyncio.run(
        Runtime(LINEAR, {"a": async_a, "b": sync_b}).arun(
            State.empty("x"), replay=ReplayStatus.declared("full"))).trace
    rows.append(("one `async def` node", *status_of(trace)))

    trace = asyncio.run(
        Runtime(LINEAR, {"a": plain_def_returning_coroutine, "b": sync_b}).arun(
            State.empty("x"), replay=ReplayStatus.declared("full"))).trace
    rows.append(("plain `def` returning a coroutine", *status_of(trace)))

    try:
        Runtime(LINEAR, {"a": async_generator_a, "b": sync_b}).run(
            State.empty("x"), replay=ReplayStatus.declared("full"))
    except NodeFailed as exc:
        rows.append(("`async def ... yield` node", *status_of(exc.trace)))

    width = max(len(r[0]) for r in rows)
    for name, declared, final in rows:
        print(f"  {name:<{width}}  declared={declared:<8}  terminal={final}")

    # Now the crux: for the node that keeps `full`, does verify refuse it?
    print("\n  does a trace that KEPT `full` survive verify()?")
    trace = asyncio.run(
        Runtime(LINEAR, {"a": plain_def_returning_coroutine, "b": sync_b}).arun(
            State.empty("x"), replay=ReplayStatus.declared("full"))).trace
    declared, final = status_of(trace)
    engine = ReplayEngine(TraceBundle(spec=LINEAR, trace=trace))
    try:
        engine.verify({"a": plain_def_returning_coroutine, "b": sync_b})
        verdict = "verify OK"
    except BaseException as exc:  # noqa: BLE001
        verdict = f"{type(exc).__name__}: {exc}"
    print(f"    declared={declared}  terminal={final}")
    print(f"    verify()  -> {verdict}")
    print(f"    => the trace claims {final} and the engine refuses to honour it"
          if final.startswith("full") else
          f"    => degraded, so the claim and the engine agree")


def part2() -> None:
    print("\n=== PART 2 — public API and packaging ===")

    def all_names(rev):
        src = subprocess.run(["git", "-C", str(ROOT), "show",
                              f"{rev}:src/vitruvyan_motus/__init__.py"],
                             capture_output=True, text=True).stdout
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "__all__" for t in node.targets):
                return [e.value for e in node.value.elts]
        return []

    v061, a72, head = all_names("v0.6.1"), all_names("a72cdf1"), list(M.__all__)
    print(f"  __all__ vs v0.6.1 : added={sorted(set(head)-set(v061))} "
          f"removed={sorted(set(v061)-set(head))}")
    print(f"  __all__ vs a72cdf1: added={sorted(set(head)-set(a72))} "
          f"removed={sorted(set(a72)-set(head))}")
    print(f"  unresolved names  : {[n for n in head if not hasattr(M, n)]}")
    print(f"  duplicates        : "
          f"{[n for n in set(head) if head.count(n) > 1]}")

    from vitruvyan_motus import ReplayError, ReplayMismatch, ReplayUnsupported
    from vitruvyan_motus import UnsafeResume, MotusError
    print(f"  ReplayUnsupported MRO: "
          f"{[c.__name__ for c in ReplayUnsupported.__mro__[:5]]}")
    print(f"  distinguishable from ReplayMismatch: "
          f"{not issubclass(ReplayUnsupported, ReplayMismatch)} / "
          f"{not issubclass(ReplayMismatch, ReplayUnsupported)}")
    print(f"  errors.__all__ has it: "
          f"{'ReplayUnsupported' in __import__('vitruvyan_motus.errors', fromlist=['x']).__all__}")

    print(f"\n  __version__ = {M.__version__!r}   "
          f"TRACE_SCHEMA_VERSION = {M.TRACE_SCHEMA_VERSION!r}")

    # what still says 0.6.1?
    hits = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-n", "-I", "-F", "0.6.1", "--",
         ":!audit", ":!.attack"],
        capture_output=True, text=True).stdout.strip().splitlines()
    print(f"\n  surviving literal '0.6.1' in tracked files ({len(hits)}):")
    for line in hits:
        print(f"    {line[:160]}")


def part3() -> None:
    print("\n=== PART 3 — 0.7.0.dev0 vs PEP 440 and requires_motus ===")
    try:
        from packaging.version import Version
        from packaging.specifiers import SpecifierSet
    except ImportError:
        print("  packaging not importable; skipping")
        return

    version = Version(M.__version__)
    print(f"  Version({M.__version__!r}) -> is_prerelease={version.is_prerelease} "
          f"is_devrelease={version.is_devrelease} release={version.release} "
          f"base_version={version.base_version}")

    specs = [
        ">=0.5,<1.0",        # what every contract fixture uses
        ">=0.6,<0.7",        # the pin you asked about
        ">=0.7,<0.8",
        ">=0.7.0.dev0,<1.0",
        ">=0.5.dev0,<1.0",
        "~=0.7.0",
        "==0.7.0.dev0",
    ]
    print(f"\n  {'specifier':<22} {'default':<9} {'prereleases=True':<17} "
          f"R12 (graph.py)")
    for spec in specs:
        s = SpecifierSet(spec)
        default = version in s
        loose = s.contains(str(version), prereleases=True)
        doc = dict(
            schema_version="1.0.0", name="v", version="1.0.0", entry="a",
            nodes=[{"name": "a", "effect_class": "pure"}],
            transitions={"a": {"kind": "terminal"}},
            requires_motus=spec,
        )
        try:
            GraphSpec.from_dict(dict(doc))
            r12 = "accepted"
        except BaseException as exc:  # noqa: BLE001
            r12 = f"rejected ({type(exc).__name__})"
        print(f"  {spec:<22} {str(default):<9} {str(loose):<17} {r12}")

    print("\n  Is requires_motus enforced against the installed runtime anywhere?")
    found = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-n", "requires_motus", "--",
         "src/"], capture_output=True, text=True).stdout.strip().splitlines()
    enforcing = [line for line in found
                 if "__version__" in line or "SpecifierSet" in line
                 or "contains" in line]
    print(f"    src/ mentions: {len(found)}; any that compare to __version__ "
          f"or use a specifier matcher: {len(enforcing)}")
    fixtures = subprocess.run(
        ["git", "-C", str(ROOT), "grep", "-l", "requires_motus", "--",
         "contract/fixtures/"], capture_output=True, text=True).stdout.split()
    print(f"    contract fixtures declaring requires_motus: {len(fixtures)}")
    values = set()
    for path in fixtures:
        doc = json.loads((ROOT / path).read_text())
        instance = doc.get("instance") or {}
        if isinstance(instance, dict) and "requires_motus" in instance:
            values.add(str(instance["requires_motus"]))
    print(f"    distinct values in fixtures: {sorted(values)}")
    for value in sorted(values):
        try:
            s = SpecifierSet(value)
            print(f"      {value!r}: matches {M.__version__} by default = "
                  f"{version in s}; with prereleases=True = "
                  f"{s.contains(str(version), prereleases=True)}")
        except BaseException as exc:  # noqa: BLE001
            print(f"      {value!r}: unparseable ({exc})")


if __name__ == "__main__":
    part1()
    part2()
    part3()
