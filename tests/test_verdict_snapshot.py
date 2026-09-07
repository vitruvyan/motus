"""The printed verdict is a tested artifact, not a side effect of the rules.

2026-09-07 round: an em dash became `--` in three EXISTENCE sentences during
the attestation work, and nothing in the suite noticed -- every existing
test asserts on a SUBSTRING or a STATUS, never on the exact bytes a reader
is shown. This snapshots `format_verdict()` over every receipt fixture in
`contract/fixtures/` so a byte the corpus tests don't happen to substring-
match still fails the suite.

Regenerate deliberately, after a wording change, with::

    MOTUS_UPDATE_SNAPSHOTS=1 .venv/bin/pytest tests/test_verdict_snapshot.py

and read the diff in review -- a snapshot that updates itself silently is
not a test.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "contract"
FIXTURES_DIR = CONTRACT_DIR / "fixtures"
SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshots" / "receipt-verdicts.txt"

_SEPARATOR = "=" * 78


def _load_validate_module():
    module_name = "motus_contract_validate"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, CONTRACT_DIR / "validate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


validate = _load_validate_module()


def _receipt_fixtures() -> list[Path]:
    """Every fixture whose artifact is `receipt`, sorted for a stable diff."""
    out = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        if json.loads(path.read_text(encoding="utf-8")).get("artifact") == "receipt":
            out.append(path)
    return out


def _render() -> str:
    """`format_verdict(verify(instance))` for every receipt fixture, in one
    deterministic document. No trace is supplied (like the corpus-diff lens
    this test formalises): the point is the TEXT, not which levels a trace
    would additionally establish."""
    blocks = []
    for path in _receipt_fixtures():
        instance = json.loads(path.read_text(encoding="utf-8"))["instance"]
        verdict = validate.verify(instance, None)
        blocks.append(f"{_SEPARATOR}\n{path.name}\n{_SEPARATOR}\n"
                      f"{validate.format_verdict(verdict)}\n")
    return "\n".join(blocks)


def test_receipt_fixture_verdicts_match_the_committed_snapshot():
    """A verdict's wording is part of the product; a diff here must be a
    reviewed, deliberate change -- never an accident of refactoring the
    string that produces it."""
    rendered = _render()
    if os.environ.get("MOTUS_UPDATE_SNAPSHOTS"):
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(rendered, encoding="utf-8")
        return
    assert SNAPSHOT_PATH.exists(), (
        f"no snapshot at {SNAPSHOT_PATH}; regenerate with "
        "MOTUS_UPDATE_SNAPSHOTS=1 .venv/bin/pytest tests/test_verdict_snapshot.py")
    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert rendered == expected, (
        "a receipt fixture's printed verdict changed -- if this is a "
        "deliberate wording change, regenerate with MOTUS_UPDATE_SNAPSHOTS=1 "
        "and review the diff; if it is not, something silently rewrote a "
        "sentence a reader is shown")
