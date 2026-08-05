"""ATTACK 4 -- the coroutine guard in `_invoke_sync` (runtime.py:166-173).

`_invoke_sync` calls `returned.close()` on an awaitable before erroring. Does
that leak a "coroutine was never awaited" RuntimeWarning? What about an async
generator, a custom `__await__` object, a Future, a Task? And is the async
driver's hygiene symmetric -- `_invoke_async` awaits exactly ONE level
(runtime.py:214-215), so what happens to an awaitable it does not await?

The child process runs with `-W error::RuntimeWarning -X dev`, so warnings
raised in normal code become exceptions and warnings raised in `__del__`
become "Exception ignored" lines on stderr. Both are inspected.
"""

from __future__ import annotations

import asyncio
import functools
import gc
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

from x1_common import PINNED, Report, load_validator  # noqa: E402

from vitruvyan_motus import GraphSpec, Runtime, State  # noqa: E402

LINEAR = {
    "schema_version": "1.0.0",
    "name": "coroguard",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}

CLOSED: list[str] = []


class AwaitableNoClose:
    def __await__(self):
        return iter(())


class AwaitableClosable:
    def close(self):
        CLOSED.append("closable")

    def __await__(self):
        return iter(())


class AwaitableCloseRaises:
    def close(self):
        raise RuntimeError("close() refused")

    def __await__(self):
        return iter(())


async def plain_async(state):
    await asyncio.sleep(0)
    return state


async def inner_async(state):
    await asyncio.sleep(0)
    return state


async def double_awaitable(state):
    """An async node whose *result* is itself an un-awaited coroutine."""
    await asyncio.sleep(0)
    return inner_async(state)


async def async_gen_node(state):
    yield state


class AsyncCallable:
    async def __call__(self, state):
        await asyncio.sleep(0)
        return state


def make_future_node(resolve, exception=False):
    def node(state):
        fut = asyncio.get_event_loop().create_future()
        if resolve:
            if exception:
                fut.set_exception(ValueError("future blew up"))
            else:
                fut.set_result(state)
        return fut

    return node


def task_node(state):
    return asyncio.get_event_loop().create_task(plain_async(state))


NODES = {
    "async-def": plain_async,
    "partial-async": functools.partial(plain_async),
    "async-__call__": AsyncCallable(),
    "async-generator-fn": async_gen_node,
    "double-awaitable": double_awaitable,
    "awaitable-no-close": lambda s: AwaitableNoClose(),
    "awaitable-closable": lambda s: AwaitableClosable(),
    "awaitable-close-raises": lambda s: AwaitableCloseRaises(),
    "resolved-future": make_future_node(resolve=True),
    "pending-future": make_future_node(resolve=False),
    "failed-future": make_future_node(resolve=True, exception=True),
    "task": task_node,
    "def-returning-coroutine": lambda s: inner_async(s),
}


def build(name):
    return Runtime(
        GraphSpec.from_dict(dict(LINEAR)),
        {"a": NODES[name], "b": lambda s: s},
        **PINNED,
    )


def snapshot(rt, err, validate):
    document = rt.trace.to_dict() if rt.trace else None
    return {
        "err": err,
        "kinds": [r["kind"] for r in rt.trace.records] if rt.trace else None,
        "error_type": next(
            (r["error"]["type"] for r in (rt.trace.records if rt.trace else [])
             if r["kind"] == "transition" and r["error"]), None
        ),
        "error_msg_names_arun": next(
            (("arun" in str(r["error"].get("message", "")))
             for r in (rt.trace.records if rt.trace else [])
             if r["kind"] == "transition" and r["error"]), None
        ),
        "violations": [str(v) for v in validate.validate_trace(document, spec=LINEAR)] if document else None,
        "jsonl_violations": (
            [str(v) for v in validate.validate_jsonl(rt.trace.to_jsonl(), spec=LINEAR)[0]]
            if document else None
        ),
        "running": rt._running,
    }


async def probe() -> None:
    validate = load_validator()
    out = {}
    for name in NODES:
        CLOSED.clear()
        # --- sync driver -------------------------------------------------
        rt = build(name)
        err = None
        try:
            rt.run(State.empty("guard"), run_id="pinned")
        except BaseException as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        sync = snapshot(rt, err, validate)
        sync["close_called"] = list(CLOSED)

        # --- async driver ------------------------------------------------
        CLOSED.clear()
        rt2 = build(name)
        err2 = None
        try:
            # A node returning a never-resolving Future would hang the async
            # driver forever -- that is itself a finding, so bound it.
            await asyncio.wait_for(
                rt2.arun(State.empty("guard"), run_id="pinned"), timeout=2.0
            )
        except BaseException as exc:  # noqa: BLE001
            err2 = f"{type(exc).__name__}: {exc}"
        asyncr = snapshot(rt2, err2, validate)
        asyncr["close_called"] = list(CLOSED)

        out[name] = {"sync": sync, "async": asyncr}
        gc.collect()
    gc.collect()
    sys.stdout.write(json.dumps(out, default=str))
    sys.stdout.flush()


def analyse() -> int:
    report = Report("x1_coroutine -- the coroutine guard under -W error::RuntimeWarning")
    env = dict(os.environ, PYTHONPATH=str(HERE))
    proc = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-X", "dev",
         str(HERE / "x1_coroutine.py"), "probe"],
        capture_output=True, text=True, env=env, cwd=str(HERE),
    )
    if proc.returncode != 0 or not proc.stdout:
        print("child failed:\n", proc.stderr[-6000:])
        return 1
    data = json.loads(proc.stdout)
    stderr = proc.stderr

    noisy_markers = ("never awaited", "was destroyed but it is pending",
                     "Exception ignored", "never retrieved", "RuntimeWarning")
    per_marker = {m: (m in stderr) for m in noisy_markers}

    for name, sides in data.items():
        s, a = sides["sync"], sides["async"]
        report.record(
            f"{name}/sync: trace is contract-valid in both encodings",
            s["violations"] == [] and s["jsonl_violations"] == [],
            f"{s['violations'][:1]} {s['jsonl_violations'][:1]}",
        )
        report.record(
            f"{name}/async: trace is contract-valid in both encodings",
            a["violations"] == [] and a["jsonl_violations"] == [],
            f"{a['violations'][:1]} {a['jsonl_violations'][:1]}",
        )
        report.record(
            f"{name}/sync: run terminated with _running False",
            s["running"] is False, f"running={s['running']}",
        )
        report.record(
            f"{name}/async: run terminated with _running False",
            a["running"] is False, f"running={a['running']}",
        )
        print(f"    . {name}: sync={s['err']} kinds={s['kinds']} errtype={s['error_type']} "
              f"names_arun={s['error_msg_names_arun']} closed={s['close_called']}")
        print(f"    . {name}: async={a['err']} kinds={a['kinds']} errtype={a['error_type']}")

    report.record(
        "no RuntimeWarning noise on stderr under -W error::RuntimeWarning -X dev",
        not any(per_marker.values()),
        json.dumps(per_marker) + " | " + stderr[-1500:].replace("\n", " | "),
    )
    failures = report.emit()
    if stderr.strip():
        print("\n--- child stderr ---")
        print(stderr[-4000:])
    return failures


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        asyncio.run(probe())
    else:
        sys.exit(1 if analyse() else 0)
