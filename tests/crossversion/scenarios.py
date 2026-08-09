"""GraphSpec bodies for the cross-version corpus, named by node in ``nodes.py``.

Kept separate from ``nodes.py`` (which is the thing whose *source* has to be
identical across eras) and from ``generate.py`` (which is the thing that
changes as versions are added), so that adding a fourth scenario later is
one function here plus one line in ``generate.py.SCENARIOS`` — not a larger
diff to either.

Each entry returns a plain dict (the GraphSpec wire form — no dependency on
which ``vitruvyan_motus`` is on ``sys.path``, so this module is safe to
import from the archived-release subprocess and from the test process
alike) and the node-name -> ``nodes.py``-function-name mapping a caller needs
to build the registry ``Runtime`` requires.
"""

from __future__ import annotations

SCENARIOS: dict[str, tuple[dict, dict[str, str]]] = {
    # verify()'s happy path: nothing but pure nodes, nothing to skip.
    "pure_only": (
        {
            "schema_version": "1.0.0",
            "name": "xv-pure-only",
            "version": "1.0.0",
            "entry": "seed",
            "nodes": [
                {"name": "seed", "effect_class": "pure", "writes_declared": ["n"]},
                {
                    "name": "double", "effect_class": "pure",
                    "reads_declared": ["n"], "writes_declared": ["doubled"],
                },
            ],
            "transitions": {
                "seed": {"kind": "next", "to": "double"},
                "double": {"kind": "terminal"},
            },
        },
        {"seed": "seed", "double": "double"},
    ),
    # A routed Decision: exercises the routing record and origin bookkeeping
    # that verify()'s bundle-semantics check recomputes independently.
    "routed_decision": (
        {
            "schema_version": "1.0.0",
            "name": "xv-routed-decision",
            "version": "1.0.0",
            "entry": "classify",
            "nodes": [
                {"name": "classify", "effect_class": "pure"},
                {"name": "big_path", "effect_class": "pure", "writes_declared": ["label"]},
                {"name": "small_path", "effect_class": "pure", "writes_declared": ["label"]},
            ],
            "transitions": {
                "classify": {
                    "kind": "route", "on": "branch",
                    "map": {"big": "big_path", "small": "small_path"},
                },
                "big_path": {"kind": "terminal"},
                "small_path": {"kind": "terminal"},
            },
        },
        {"classify": "classify", "big_path": "big_path", "small_path": "small_path"},
    ),
    # A recorded_effect with a receipt, followed by a pure node reading its
    # write — verify() must skip re-executing "fetch" and still re-execute
    # "summarize" against what "fetch" actually committed.
    "recorded_effect": (
        {
            "schema_version": "1.0.0",
            "name": "xv-recorded-effect",
            "version": "1.0.0",
            "entry": "fetch",
            "nodes": [
                {
                    "name": "fetch", "effect_class": "recorded_effect",
                    "writes_declared": ["payload"],
                },
                {
                    "name": "summarize", "effect_class": "pure",
                    "reads_declared": ["payload"], "writes_declared": ["summary"],
                },
            ],
            "transitions": {
                "fetch": {"kind": "next", "to": "summarize"},
                "summarize": {"kind": "terminal"},
            },
        },
        {"fetch": "fetch", "summarize": "summarize"},
    ),
}
