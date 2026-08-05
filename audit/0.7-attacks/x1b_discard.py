"""ROUND 2, ITEM 1 -- attack `_discard_awaitable` and the relocated guard.

`_invoke_sync` now does the awaitable check INSIDE its try/except Exception
(runtime.py:174-188) and delegates cleanup to `_discard_awaitable`
(runtime.py:139-150):

    closer = getattr(value, "close", None)
    if callable(closer):
        closer()

Three assumptions are baked into those three lines:
  (i)   `close` is either absent or a callable that does the cleanup;
  (ii)  calling it is synchronous and complete;
  (iii) whatever it raises is an ordinary node failure.

Each is attacked below with a node shape that violates it. Every shape runs
in its OWN subprocess under `-W error::RuntimeWarning -X dev`, so a leaked
awaitable is attributed to exactly one path, and each is compared against the
pre-fix tree (51e2439) to separate "the fix changed this" from "this was
always so".
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
PREFIX_SRC = os.environ.get("MOTUS_PREFIX_SRC", f"{SCRATCH}/prefix/src")

from x1_common import PINNED, Report, diff, load_validator  # noqa: E402

from vitruvyan_motus import GraphSpec, Runtime, State  # noqa: E402

LINEAR = {
    "schema_version": "1.0.0",
    "name": "discard",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}, {"name": "b", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "next", "to": "b"}, "b": {"kind": "terminal"}},
}


async def _noop():
    return None


class _Aw:
    """Base: awaitable, resolves to None."""

    def __await__(self):
        return iter(())


class CloseNotCallable(_Aw):
    close = 3                      # (i) exists, is not callable


class CloseIsAsync(_Aw):
    async def close(self):         # (ii) cleanup is itself awaitable
        return None


class CloseReturnsCoroutine(_Aw):
    def close(self):               # (ii) sync close whose RESULT is awaitable
        return _noop()


class CloseAttrAttributeError(_Aw):
    @property
    def close(self):
        raise AttributeError("no close here")


class CloseAttrKeyError(_Aw):
    @property
    def close(self):
        raise KeyError("hostile __getattr__")


class CloseAttrBaseException(_Aw):
    @property
    def close(self):
        raise KeyboardInterrupt("hostile __getattr__")


class CloseRaisesException(_Aw):
    def close(self):               # (iii) ordinary Exception
        raise RuntimeError("close refused")


class CloseRaisesBaseException(_Aw):
    def close(self):               # (iii) BaseException
        raise KeyboardInterrupt("close refused")


class CloseIsStatic(_Aw):
    @staticmethod
    def close():
        CLOSED.append("static")


CLOSED: list[str] = []


async def plain_async(state):
    await asyncio.sleep(0)
    return state


async def stubborn(state):
    try:
        await asyncio.sleep(0)
    except GeneratorExit:
        await asyncio.sleep(0)
    return state


def node_started_stubborn(state):
    coro = stubborn(state)
    coro.send(None)
    return coro


async def agen(state):
    yield state


def node_asend(state):
    """An `async_generator_asend` object: awaitable, and it has no `close`."""
    return agen(state).asend(None)


def node_failed_future(state):
    fut = asyncio.get_event_loop().create_future()
    fut.set_exception(ValueError("future blew up"))
    return fut


SHAPES = {
    "close-not-callable": lambda s: CloseNotCallable(),
    "close-is-async-def": lambda s: CloseIsAsync(),
    "close-returns-coroutine": lambda s: CloseReturnsCoroutine(),
    "close-attr-raises-AttributeError": lambda s: CloseAttrAttributeError(),
    "close-attr-raises-KeyError": lambda s: CloseAttrKeyError(),
    "close-attr-raises-BaseException": lambda s: CloseAttrBaseException(),
    "close-raises-Exception": lambda s: CloseRaisesException(),
    "close-raises-BaseException": lambda s: CloseRaisesBaseException(),
    "close-is-staticmethod": lambda s: CloseIsStatic(),
    "started-coroutine-ignores-GeneratorExit": node_started_stubborn,
    "async-generator-asend-object": node_asend,
    "async-generator-function": agen,
    "failed-future": node_failed_future,
    "plain-async-def": plain_async,
}


def probe(shape: str, driver: str) -> None:
    validate = load_validator()
    rt = Runtime(
        GraphSpec.from_dict(dict(LINEAR)),
        {"a": SHAPES[shape], "b": lambda s: s},
        **PINNED,
    )
    err = None

    async def go():
        nonlocal err
        try:
            if driver == "sync":
                rt.run(State.empty("d"), run_id="pinned")
            else:
                await asyncio.wait_for(rt.arun(State.empty("d"), run_id="pinned"), 3.0)
        except BaseException as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"

    asyncio.run(go())
    import gc

    gc.collect()
    document = rt.trace.to_dict()
    payload = {
        "err": err,
        "kinds": [r["kind"] for r in rt.trace.records],
        "error_type": next(
            (r["error"]["type"] for r in rt.trace.records
             if r["kind"] == "transition" and r["error"]), None),
        "running": rt._running,
        "closed_marker": list(CLOSED),
        "violations": [str(v) for v in validate.validate_trace(document, spec=LINEAR)],
        "jsonl": [str(v) for v in
                  validate.validate_jsonl(rt.trace.to_jsonl(), spec=LINEAR)[0]],
    }
    sys.stdout.write(json.dumps(payload, default=str))
    sys.stdout.flush()


MARKERS = ("never awaited", "never retrieved", "was destroyed but it is pending",
           "Exception ignored")


def run_probe(shape: str, driver: str, src: str | None = None):
    env = dict(os.environ, PYTHONPATH=str(HERE))
    if src:
        env["MOTUS_SRC"] = src
    proc = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-X", "dev",
         str(HERE / "x1b_discard.py"), "probe", shape, driver],
        capture_output=True, text=True, env=env, cwd=str(HERE), timeout=120,
    )
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {"crashed": proc.stderr[-400:]}
    hits = [m for m in MARKERS if m in proc.stderr]
    lines = [l for l in proc.stderr.splitlines() if any(m in l for m in MARKERS)]
    return payload, hits, lines


def main() -> int:
    report = Report("x1b_discard -- _discard_awaitable and the relocated guard")
    have_prefix = Path(PREFIX_SRC).is_dir()
    if not have_prefix:
        print(f"(pre-fix tree missing at {PREFIX_SRC}; skipping pre/post comparison)")

    for shape in SHAPES:
        s_payload, s_hits, s_lines = run_probe(shape, "sync")
        a_payload, a_hits, a_lines = run_probe(shape, "async")

        report.record(
            f"{shape}/sync: no interpreter-level awaitable warning",
            not s_hits, f"{s_hits} :: " + " | ".join(s_lines)[:220],
        )
        report.record(
            f"{shape}/async: no interpreter-level awaitable warning",
            not a_hits, f"{a_hits} :: " + " | ".join(a_lines)[:220],
        )
        report.record(
            f"{shape}/sync: an ordinary Exception stays inside the node-failure "
            "boundary (closed attempt, run_failed)",
            # A BaseException legitimately tears down (node-protocol 7.3); an
            # Exception must not.
            ("BaseException" in shape)
            or s_payload.get("kinds") == ["run_started", "attempt_started",
                                          "transition", "run_failed"],
            f"err={s_payload.get('err')} kinds={s_payload.get('kinds')}",
        )
        report.record(
            f"{shape}/sync: trace contract-valid in both encodings",
            s_payload.get("violations") == [] and s_payload.get("jsonl") == [],
            f"{s_payload.get('violations', [])[:1]} {s_payload.get('jsonl', [])[:1]}",
        )
        print(f"    . {shape:42s} sync={s_payload.get('err')} "
              f"kinds={s_payload.get('kinds')} type={s_payload.get('error_type')}")
        print(f"    . {'':42s} async={a_payload.get('err')} "
              f"kinds={a_payload.get('kinds')} type={a_payload.get('error_type')}")

        if have_prefix:
            p_payload, p_hits, _ = run_probe(shape, "sync", src=PREFIX_SRC)
            changed = diff(p_payload, s_payload)
            if changed or p_hits != s_hits:
                print(f"    ~ {shape}: CHANGED by the fix -- pre={p_payload.get('err')} "
                      f"kinds={p_payload.get('kinds')} warn={p_hits} "
                      f"=> post={s_payload.get('err')} kinds={s_payload.get('kinds')} "
                      f"warn={s_hits}")

    return report.emit()


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "probe":
        probe(sys.argv[2], sys.argv[3])
    else:
        sys.exit(1 if main() else 0)
