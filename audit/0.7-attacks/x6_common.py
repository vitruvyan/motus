"""Shared fixtures for the x6_* round: JsonlTraceSink (the shipped durable sink).

The target is `src/vitruvyan_motus/sinks.py`.  Everything here talks to it as a
user would -- through `Runtime(..., sink=JsonlTraceSink(dir))` -- and checks the
files it leaves behind against `contract/validate.py`, which is the only thing
that gets to say whether an artifact is an artifact.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vitruvyan_motus import (
    Decision, DurabilityProfile, Fact, GraphSpec, JsonlTraceSink, Rejection,
    Runtime, State,
)

REPO = Path(__file__).resolve().parents[1]
VALIDATE = REPO / "contract" / "validate.py"
PYTHON = REPO / ".venv" / "bin" / "python"
OUT = Path(os.environ.get("X6_OUT", tempfile.mkdtemp(prefix="x6-artifacts-")))
OUT.mkdir(parents=True, exist_ok=True)

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)

SYNC = DurabilityProfile.SYNCHRONOUS
BUFFERED = DurabilityProfile.BUFFERED
INMEM = DurabilityProfile.IN_MEMORY


# --------------------------------------------------------------------------- #
# Graphs                                                                      #
# --------------------------------------------------------------------------- #

CHAIN_DOC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "name": "x6-chain",
    "version": "1.0.0",
    "entry": "a",
    "nodes": [
        {"name": "a", "effect_class": "pure"},
        {"name": "b", "effect_class": "pure"},
        {"name": "c", "effect_class": "pure"},
    ],
    "transitions": {
        "a": {"kind": "next", "to": "b"},
        "b": {"kind": "next", "to": "c"},
        "c": {"kind": "terminal"},
    },
}
CHAIN = GraphSpec.from_dict(dict(CHAIN_DOC))


def note(state: State) -> State:
    return state.with_fact(Fact("seen", state.intent, "x6", NOW))


def passthrough(state: State) -> State:
    return state


def registry() -> dict[str, Any]:
    return {"a": note, "b": passthrough, "c": passthrough}


def runtime(sink=None, profile=SYNC, nodes=None, **kwargs: Any) -> Runtime:
    return Runtime(
        CHAIN, nodes or registry(), sink=sink, durability_profile=profile, **kwargs
    )


def spec_path(name: str = "x6-chain.graphspec.json") -> Path:
    path = OUT / name
    if not path.exists():
        path.write_text(
            json.dumps(CHAIN.to_dict(), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    return path


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def validate_file(path: Path, *, spec: Path | None = None,
                  allow_incomplete: bool = False) -> tuple[int, str]:
    """Run the real CLI, exactly as an auditor would."""
    cmd = [str(PYTHON), str(VALIDATE), "jsonl", str(path)]
    if spec is not None:
        cmd += ["--spec", str(spec)]
    if allow_incomplete:
        cmd += ["--allow-incomplete"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def classify(path: Path) -> str:
    name = path.name
    if name.endswith(".jsonl.part"):
        return "part"
    if name.endswith(".partial.jsonl"):
        return "partial"
    if name.endswith(".jsonl"):
        return "final"
    return "other"


def validate_expected(path: Path, *, spec: Path | None = None) -> tuple[bool, str]:
    """Validate in the mode the file's own name claims for it."""
    kind = classify(path)
    if kind == "final":
        code, out = validate_file(path, spec=spec)
        return code == 0, out
    if kind in ("partial", "part"):
        code, out = validate_file(path, spec=spec, allow_incomplete=True)
        return code == 0, out
    return False, f"unclassifiable artifact name {path.name}"


def listing(directory: Path) -> list[str]:
    return sorted(p.name for p in Path(directory).iterdir())


# --------------------------------------------------------------------------- #
# Reporting                                                                   #
# --------------------------------------------------------------------------- #


class Report:
    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, str, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, "OK" if ok else "BROKEN", detail))
        print(f"[{'OK    ' if ok else 'BROKEN'}] {name}  {detail}", flush=True)

    def note(self, text: str) -> None:
        print(f"[note  ] {text}", flush=True)

    def dump(self) -> int:
        width = max((len(r[0]) for r in self.rows), default=10)
        print(f"\n=== {self.title} ===   artifacts: {OUT}")
        for name, verdict, detail in self.rows:
            print(f"{name.ljust(width)}  {verdict}  {detail}")
        broken = sum(1 for r in self.rows if r[1] == "BROKEN")
        print(f"-- {len(self.rows)} checks, {broken} BROKEN")
        return broken


def scratch(name: str) -> Path:
    path = OUT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


if __name__ == "__main__":
    print("artifacts dir:", OUT)
    print("python:", sys.version)
