"""The single-source-of-truth check for the trace schema version.

Required by `contract/README.md`'s "One source per fact" rule and by
ADR-003 §Decision 2, which places this test outside both frozen corpora
because the package it tests postdates them: `tests/contract/` is frozen
and cannot host a test for code that did not exist when it was written.

This is not a milestone-A implementation detail — it is a standing
contract obligation. It must keep passing for every schema-version-bearing
commit from here forward.
"""

from __future__ import annotations

import json
from pathlib import Path

from vitruvyan_motus import TRACE_SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACE_SCHEMA = REPO_ROOT / "contract" / "trace.v1.schema.json"


def test_package_trace_schema_version_matches_the_contract_schema():
    schema = json.loads(TRACE_SCHEMA.read_text(encoding="utf-8"))
    contract_const = schema["x-current-version"]

    assert TRACE_SCHEMA_VERSION == contract_const, (
        f"vitruvyan_motus.TRACE_SCHEMA_VERSION ({TRACE_SCHEMA_VERSION!r}) has "
        f"drifted from contract/trace.v1.schema.json's x-current-version "
        f"({contract_const!r}) — these must never disagree"
    )


def test_trace_schema_version_is_not_the_distribution_version():
    """The two version numbers answer different questions (see
    vitruvyan_motus/__init__.py) and must never be collapsed into one
    constant — this pins that they are, in fact, two distinct names."""
    import vitruvyan_motus

    assert TRACE_SCHEMA_VERSION != vitruvyan_motus.__version__
