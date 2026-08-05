"""ROUND 3, ITEMS 1/2/4 -- the restructured `verify`, `_is_asynchronous`,
and the new public `ReplayUnsupported`.

replay.py:397-416 now reads:

    try:    returned = node(...)
    except ReplayMismatch: raise
    except BaseException as exc: raised = exc
    if raised is None and _is_asynchronous(returned):
        _discard_unreplayable(returned); raise ReplayUnsupported(...)

Attacks:
  * did `pure-raises-ReplayError` / `pure-raises-UnsafeResume` return EXACTLY
    to their 51e2439 behaviour? (three-tree differential, subprocesses)
  * is `ReplayUnsupported` the same trap one level down -- a node that raises
    it now that it is public?
  * `_is_asynchronous = isawaitable or isasyncgen`: what does it still miss?
    A hand-rolled async iterator and an `@asynccontextmanager` result are both
    "asynchronous" to a reader and invisible to that predicate.
  * the refusal moved OUTSIDE the boundary, so `_discard_unreplayable`
    raising now escapes verify raw. Measured, not assumed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = ("/tmp/claude-1000/-home-vitruvyan-motus/"
           "2eec55a9-be04-4965-b2c4-951aee158e49/scratchpad")
TREES = {
    "51e2439": f"{SCRATCH}/prefix/src",
    "a72cdf1": f"{SCRATCH}/h2/src",
    "fb30b8d": f"{SCRATCH}/h3/src",
}

from x1_common import FIXED_TS, PINNED, Report, diff  # noqa: E402

import vitruvyan_motus as M  # noqa: E402
from vitruvyan_motus import (  # noqa: E402
    Fact, GraphSpec, Policy, ReplayEngine, ReplayError, ReplayMismatch,
    Runtime, State, Trace, TraceBundle, UnsafeResume,
)

ReplayUnsupported = getattr(M, "ReplayUnsupported", None)

SPEC_DOC = {
    "schema_version": "1.0.0",
    "name": "verify3",
    "version": "1.0.0",
    "entry": "p",
    "nodes": [{"name": "p", "effect_class": "pure"}],
    "transitions": {"p": {"kind": "terminal"}},
}
SPEC = GraphSpec.from_dict(dict(SPEC_DOC))


# --------------------------------------------------------------------------- #
# Node shapes                                                                  #
# --------------------------------------------------------------------------- #


def ok_pure(state):
    return state.with_fact(Fact("p", 1, "s", FIXED_TS))


def diverged_pure(state):
    return state.with_fact(Fact("p", 999, "s", FIXED_TS))


def raises_replay_error(state):
    raise ReplayError("a node's own ReplayError")


def raises_unsafe_resume(state):
    raise UnsafeResume("a node's own UnsafeResume")


def raises_replay_mismatch(state):
    raise ReplayMismatch("p", 3, "field")


def raises_replay_unsupported(state):
    if ReplayUnsupported is None:
        raise ReplayError("a node's own ReplayError")   # pre-fb30b8d stand-in
    raise ReplayUnsupported("a node's own ReplayUnsupported")


def raises_value_error(state):
    raise ValueError("ordinary")


async def async_pure(state):
    await asyncio.sleep(0)
    return state.with_fact(Fact("p", 1, "s", FIXED_TS))


async def async_gen_pure(state):
    yield state.with_fact(Fact("p", 1, "s", FIXED_TS))


class AwaitOnly:
    """Awaitable but not a coroutine."""

    def __await__(self):
        return iter(())


def await_only_pure(state):
    return AwaitOnly()


class AsyncIteratorOnly:
    """`async for`-able. No `__await__`, not an async generator object."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def async_iterator_pure(state):
    return AsyncIteratorOnly()


@contextlib.asynccontextmanager
async def _acm():
    yield 1


def async_cm_pure(state):
    return _acm()          # _AsyncGeneratorContextManager: neither predicate hits


class CloseRaises:
    def close(self):
        raise RuntimeError("cleanup refused")

    def __await__(self):
        return iter(())


def close_raises_pure(state):
    return CloseRaises()


def future_pure(state):
    fut = asyncio.new_event_loop().create_future()
    fut.set_exception(ValueError("boom"))
    return fut


CASES = {
    "sync-clean": (ok_pure, ok_pure, Policy.STRICT),
    "pure-raises-ReplayError": (raises_replay_error, raises_replay_error, Policy.EXPLORATION),
    "pure-raises-UnsafeResume": (raises_unsafe_resume, raises_unsafe_resume, Policy.EXPLORATION),
    "pure-raises-ReplayMismatch": (raises_replay_mismatch, raises_replay_mismatch, Policy.EXPLORATION),
    "pure-raises-ReplayUnsupported": (raises_replay_unsupported, raises_replay_unsupported, Policy.EXPLORATION),
    "pure-raises-ValueError": (raises_value_error, raises_value_error, Policy.EXPLORATION),
    "true-divergence": (ok_pure, diverged_pure, Policy.STRICT),
    "async-pure": (async_pure, async_pure, Policy.EXPLORATION),
    "async-generator-pure": (async_gen_pure, async_gen_pure, Policy.EXPLORATION),
    "await-only-object": (await_only_pure, await_only_pure, Policy.EXPLORATION),
    "async-iterator-only": (async_iterator_pure, async_iterator_pure, Policy.EXPLORATION),
    "asynccontextmanager-result": (async_cm_pure, async_cm_pure, Policy.EXPLORATION),
    "awaitable-whose-close-raises": (close_raises_pure, close_raises_pure, Policy.EXPLORATION),
    "future-with-exception": (future_pure, future_pure, Policy.EXPLORATION),
}


def dump() -> None:
    out = {}
    for label, (record_node, verify_node, policy) in CASES.items():
        rt = Runtime(SPEC, {"p": record_node}, policy=policy, **PINNED)
        try:
            rt.run(State.empty("v"), run_id="src")
        except BaseException:  # noqa: BLE001
            pass
        document = rt.trace.to_dict()
        transitions = [
            (r["outcome"], r["error"] and r["error"]["type"])
            for r in document["records"] if r["kind"] == "transition"
        ]
        try:
            result = ReplayEngine(
                TraceBundle(SPEC, Trace.from_dict(document))).verify({"p": verify_node})
            verdict = {"ok": True, "verified": [list(v) for v in result.verified]}
        except BaseException as exc:  # noqa: BLE001
            verdict = {"ok": False, "exc": type(exc).__name__, "msg": str(exc)[:130]}
        out[label] = {"recorded": transitions, "verify": verdict}
    sys.stdout.write(json.dumps(out, default=str))


MARKERS = ("never awaited", "never retrieved", "was destroyed but it is pending",
           "Exception ignored")


def run_side(src: str):
    env = dict(os.environ, PYTHONPATH=str(HERE), MOTUS_SRC=src)
    proc = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-X", "dev",
         str(HERE / "x1c_verify.py"), "dump"],
        capture_output=True, text=True, env=env, cwd=str(HERE), timeout=300,
    )
    if not proc.stdout.strip():
        return None, proc.stderr
    return json.loads(proc.stdout), proc.stderr


def main() -> int:
    report = Report("x1c_verify -- restructured verify, _is_asynchronous, ReplayUnsupported")
    sides, errs = {}, {}
    for label, src in TREES.items():
        if not Path(src).is_dir():
            print(f"missing tree {label} at {src}")
            return 1
        sides[label], errs[label] = run_side(src)
        if sides[label] is None:
            print(f"{label} dump failed:\n{errs[label][-2500:]}")
            return 1

    for label in CASES:
        print(f"    . {label}")
        for tree in TREES:
            print(f"        {tree}: {json.dumps(sides[tree][label]['verify'])[:150]}")

    # ---- 1. the regression must be fully reverted -------------------------
    for label in ("sync-clean", "pure-raises-ReplayError", "pure-raises-UnsafeResume",
                  "pure-raises-ReplayMismatch", "pure-raises-ValueError",
                  "true-divergence"):
        d = diff(sides["51e2439"][label], sides["fb30b8d"][label])
        report.record(
            f"{label}: fb30b8d == 51e2439 exactly (the X1B-2 regression is gone)",
            not d, "; ".join(d[:4]),
        )

    # ---- 2. ReplayUnsupported must not be the same trap one level down ----
    v = sides["fb30b8d"]["pure-raises-ReplayUnsupported"]
    report.record(
        "a node raising ReplayUnsupported is MATCHED against the recorded "
        "error.type, not swallowed or escaped",
        v["verify"]["ok"] is True,
        f"recorded={v['recorded']} verify={v['verify']}",
    )
    report.record(
        "a node raising ReplayMismatch still escapes verify "
        "[PRE-EXISTING at bb886e6/51e2439, unchanged]",
        sides["fb30b8d"]["pure-raises-ReplayMismatch"]["verify"]["ok"] is False
        and sides["51e2439"]["pure-raises-ReplayMismatch"]["verify"]["ok"] is False,
        "",
    )

    # ---- 3. the predicate ---------------------------------------------------
    for label in ("async-pure", "async-generator-pure", "await-only-object",
                  "future-with-exception"):
        v = sides["fb30b8d"][label]["verify"]
        report.record(
            f"{label}: refused with ReplayUnsupported (not a false ReplayMismatch)",
            v["ok"] is False and v.get("exc") == "ReplayUnsupported",
            json.dumps(v)[:190],
        )
    for label in ("async-iterator-only", "asynccontextmanager-result"):
        v = sides["fb30b8d"][label]["verify"]
        report.record(
            f"{label}: an asynchronous return the predicate does not detect is "
            "still refused rather than reported as a divergence",
            v["ok"] is False and v.get("exc") == "ReplayUnsupported",
            json.dumps(v)[:190],
        )

    v = sides["fb30b8d"]["awaitable-whose-close-raises"]["verify"]
    report.record(
        "an awaitable whose close() raises: verify reports a replay-domain "
        "error, not a raw third-party exception",
        v["ok"] is False and v.get("exc") in ("ReplayUnsupported", "ReplayMismatch"),
        f"{json.dumps(v)[:190]} (a72cdf1 gave "
        f"{json.dumps(sides['a72cdf1']['awaitable-whose-close-raises']['verify'])[:80]})",
    )

    # ---- 4. no leaks --------------------------------------------------------
    hits = [m for m in MARKERS if m in errs["fb30b8d"]]
    report.record(
        "verify leaks nothing under -W error::RuntimeWarning -X dev",
        not hits,
        f"{hits} :: " + " | ".join(
            l for l in errs["fb30b8d"].splitlines() if any(m in l for m in MARKERS))[:300],
    )

    # ---- 5. the new public symbol ------------------------------------------
    report.record(
        "ReplayUnsupported is importable and exported",
        ReplayUnsupported is not None and "ReplayUnsupported" in M.__all__, "",
    )
    report.record(
        "ReplayUnsupported is a ReplayError but NOT a ReplayMismatch "
        "(the two stay distinguishable)",
        ReplayUnsupported is not None
        and issubclass(ReplayUnsupported, ReplayError)
        and not issubclass(ReplayUnsupported, ReplayMismatch)
        and not issubclass(ReplayMismatch, ReplayUnsupported), "",
    )
    readme = (HERE.parent / "README.md").read_text()
    report.record(
        "README's failure-type list names the new public failure type",
        "ReplayUnsupported" in readme,
        "README.md:395 lists NodeFailed, SinkFailed, UnsafeResume, ReplayMismatch "
        "-- ReplayUnsupported is missing",
    )
    contract_text = "\n".join(
        p.read_text() for p in (HERE.parent / "contract").glob("*.md"))
    adr_text = "\n".join(p.read_text() for p in (HERE.parent / "adr").glob("*.md"))
    report.record(
        "no contract/ADR text names a replay error type that verify no longer raises",
        "ReplayMismatch" not in contract_text or "ReplayUnsupported" in contract_text,
        f"contract names ReplayMismatch={'ReplayMismatch' in contract_text} "
        f"ReplayUnsupported={'ReplayUnsupported' in contract_text}; "
        f"adr names ReplayMismatch={'ReplayMismatch' in adr_text}",
    )

    if errs["fb30b8d"].strip():
        print("\n--- fb30b8d child stderr ---")
        print(errs["fb30b8d"][-2000:])
    return report.emit()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        dump()
    else:
        sys.exit(1 if main() else 0)
