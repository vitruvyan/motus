"""Point a reused test venv at this checkout's authoritative contract tree.

The local Windows venv belongs to Motus 0.18; this isolated startup hook lets
the mutation harness use its pytest installation without changing that venv.
"""
from pathlib import Path

import vitruvyan_motus.contract as contract

_root = Path(__file__).resolve().parents[3]
contract.__path__.insert(0, str(_root / "contract"))
