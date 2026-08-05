"""Shared harness for the x1_* falsification suite.

Everything nondeterministic in a Motus run flows through three injectable
sources (clock, identity, random_source) plus the caller-supplied run_id, so
a fully pinned Runtime produces a trace that must be byte-identical across
drivers -- code_fingerprint included, when the *same* node registry is driven
both ways.  That is a strictly stronger comparison than the repo's own
tests/test_motus_async.py, which pops ts/created_ts and pins
code_fingerprint away.
"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
# MOTUS_SRC lets the differential harness point the very same case matrix at
# the pre-inversion package extracted from git, without editing anything.
sys.path.insert(0, os.environ.get("MOTUS_SRC", str(ROOT / "src")))

FIXED_TS = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
FIXED_ID = "00000000-0000-4000-8000-000000000000"


def load_validator():
    spec = importlib.util.spec_from_file_location(
        "x1_contract_validate", ROOT / "contract" / "validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PINNED = dict(
    clock=lambda: FIXED_TS,
    identity=lambda: FIXED_ID,
    random_source=lambda: 0.25,
)


# --------------------------------------------------------------------------- #
# One sync and one async node body, both delegating to the same behaviour.     #
# The registries therefore differ only in how the runtime obtains the result.  #
# --------------------------------------------------------------------------- #

BEHAVIOURS: dict[str, Callable[[Any, Any], Any]] = {}


def dispatch(name, state, ctx):
    return BEHAVIOURS[name](state, ctx)


async def adispatch(name, state, ctx):
    await asyncio.sleep(0)
    return BEHAVIOURS[name](state, ctx)


def sync_registry(names):
    return {n: functools.partial(dispatch, n) for n in names}


def async_registry(names):
    return {n: functools.partial(adispatch, n) for n in names}


# --------------------------------------------------------------------------- #
# Deep field-by-field diffing                                                  #
# --------------------------------------------------------------------------- #


def diff(a: Any, b: Any, path: str = "$", out: list[str] | None = None) -> list[str]:
    """Every differing leaf, with its exact JSON path."""
    if out is None:
        out = []
    if type(a) is not type(b) and not (
        isinstance(a, (int, float)) and isinstance(b, (int, float))
    ):
        out.append(f"{path}: type {type(a).__name__} != {type(b).__name__}")
        return out
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                out.append(f"{path}.{key}: missing on left (right={b[key]!r})")
            elif key not in b:
                out.append(f"{path}.{key}: missing on right (left={a[key]!r})")
            else:
                diff(a[key], b[key], f"{path}.{key}", out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        for i in range(min(len(a), len(b))):
            diff(a[i], b[i], f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


def doc(trace) -> dict:
    return json.loads(json.dumps(trace.to_dict()))


def pin_fingerprint(document: dict) -> dict:
    document = json.loads(json.dumps(document))
    document["run"]["graph"]["code_fingerprint"] = "<pinned>"
    return document


class Report:
    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, str, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, "PASS" if ok else "FAIL", detail))

    def emit(self) -> int:
        print(f"\n===== {self.title} =====")
        failures = 0
        for name, verdict, detail in self.rows:
            print(f"[{verdict}] {name}" + (f"  --  {detail}" if detail else ""))
            if verdict == "FAIL":
                failures += 1
        print(f"----- {len(self.rows)} checks, {failures} FAIL -----")
        return failures
