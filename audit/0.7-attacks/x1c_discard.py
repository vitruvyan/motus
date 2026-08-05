"""ROUND 3, ITEM 3 -- the two-level `close()` in `_discard_awaitable`
(runtime.py:147-165) and `_discard_unreplayable` (replay.py:31-44).

    result = closer()
    if inspect.isawaitable(result):
        inner = getattr(result, "close", None)
        if callable(inner): inner()

Two levels is a fixed depth, and the inner test is `inspect.isawaitable`, not
the `_is_asynchronous` the same commit introduced next door. Attacks:
  * three levels: an `async def close()` whose own close returns an awaitable;
  * `close()` returning a NON-awaitable object that still owns a `close`;
  * `close()` returning an async GENERATOR (isawaitable is False for those);
  * the inner `close()` raising, at both call sites.

Also re-runs the whole x1b_discard shape matrix against fb30b8d so every
remaining interpreter-level warning is attributed.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = ("/tmp/claude-1000/-home-vitruvyan-motus/"
           "2eec55a9-be04-4965-b2c4-951aee158e49/scratchpad")

from x1_common import PINNED, Report  # noqa: E402

from vitruvyan_motus import GraphSpec, Runtime, State  # noqa: E402

LINEAR = {
    "schema_version": "1.0.0",
    "name": "discard3",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}


async def _leaf():
    return None


class _Aw:
    def __await__(self):
        return iter(())


class Level3(_Aw):
    """close() -> awaitable whose own close() -> another awaitable."""

    def close(self):
        return _Mid()


class _Mid:
    """Awaitable; its close() hands back yet another coroutine."""

    def __await__(self):
        return iter(())

    def close(self):
        return _leaf()          # level 3 -- never closed by a 2-level walker


class CloseReturnsClosable(_Aw):
    """close() -> a NON-awaitable object that nevertheless owns a close()."""

    def close(self):
        return _NonAwaitableClosable()


class _NonAwaitableClosable:
    def __init__(self):
        self.coro = _leaf()     # holds a coroutine it would have closed

    def close(self):
        self.coro.close()


async def _agen_close():
    yield 1


class CloseReturnsAsyncGen(_Aw):
    """close() -> an async generator: isawaitable() is False for those."""

    def close(self):
        gen = _agen_close()
        return gen


class InnerCloseRaises(_Aw):
    def close(self):
        return _RaisingInner()


class _RaisingInner:
    def __await__(self):
        return iter(())

    def close(self):
        raise RuntimeError("inner cleanup refused")


SHAPES = {
    "three-level-close": lambda s: Level3(),
    "close-returns-non-awaitable-closable": lambda s: CloseReturnsClosable(),
    "close-returns-async-generator": lambda s: CloseReturnsAsyncGen(),
    "inner-close-raises": lambda s: InnerCloseRaises(),
}

MARKERS = ("never awaited", "never retrieved", "was destroyed but it is pending",
           "Exception ignored")


def probe(shape: str) -> None:
    rt = Runtime(
        GraphSpec.from_dict(dict(LINEAR)),
        {"a": SHAPES[shape], "b": lambda s: s}, **PINNED,
    )
    err = None
    try:
        rt.run(State.empty("d"), run_id="pinned")
    except BaseException as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
    import gc

    gc.collect()
    sys.stdout.write(json.dumps({
        "err": err,
        "kinds": [r["kind"] for r in rt.trace.records],
        "error_type": next(
            (r["error"]["type"] for r in rt.trace.records
             if r["kind"] == "transition" and r["error"]), None),
        "running": rt._running,
    }))
    sys.stdout.flush()


def run_probe(shape: str, src: str | None = None):
    env = dict(os.environ, PYTHONPATH=str(HERE))
    if src:
        env["MOTUS_SRC"] = src
    proc = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-X", "dev",
         str(HERE / "x1c_discard.py"), "probe", shape],
        capture_output=True, text=True, env=env, cwd=str(HERE), timeout=120,
    )
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {"crashed": proc.stderr[-300:]}
    hits = [m for m in MARKERS if m in proc.stderr]
    lines = [l for l in proc.stderr.splitlines() if any(m in l for m in MARKERS)]
    return payload, hits, lines


def main() -> int:
    report = Report("x1c_discard -- the two-level close walker")

    for shape in SHAPES:
        payload, hits, lines = run_probe(shape)
        report.record(
            f"{shape}: nothing leaks under -W error::RuntimeWarning -X dev",
            not hits, f"{hits} :: " + " | ".join(lines)[:220],
        )
        report.record(
            f"{shape}: cleanup failure stays inside the node-failure boundary",
            payload.get("kinds") == ["run_started", "attempt_started",
                                     "transition", "run_failed"],
            f"err={payload.get('err')} kinds={payload.get('kinds')}",
        )
        print(f"    . {shape:38s} err={payload.get('err')} "
              f"type={payload.get('error_type')}")

    # ---- re-run the whole round-two shape matrix on fb30b8d ---------------
    print("\n--- delegating the 14-shape matrix to x1b_discard on fb30b8d ---")
    proc = subprocess.run(
        [sys.executable, str(HERE / "x1b_discard.py")],
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=str(HERE), MOTUS_SRC=f"{SCRATCH}/h3/src"),
        cwd=str(HERE), timeout=900,
    )
    fails = [l for l in proc.stdout.splitlines() if l.startswith("[FAIL]")]
    warn_fails = [l for l in fails if "awaitable warning" in l]
    summary = [l for l in proc.stdout.splitlines() if "checks," in l]
    report.record(
        "x1b_discard on fb30b8d: no remaining awaitable-warning failures",
        not warn_fails,
        (summary[-1] if summary else "") + " || " + " || ".join(warn_fails)[:400],
    )
    for line in fails:
        print("    " + line[:200])
    return report.emit()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "probe":
        probe(sys.argv[2])
    else:
        sys.exit(1 if main() else 0)
