"""Node functions shared by the cross-version corpus.

Every scenario in ``tests/crossversion/`` is executed twice: once inside an
archived release, to produce the trace document committed alongside its
graph spec, and once later, when ``tests/test_crossversion_verify.py`` asks
:class:`~vitruvyan_motus.ReplayEngine` to re-execute it under whatever Motus
is installed today. Both executions import this same file.

Binding ``from vitruvyan_motus import ...`` at module scope is deliberate,
not an oversight: it is what lets one source file stand for "the exact node
code" in both eras. ``generate.py`` runs its child process with the archived
release first on ``sys.path``, so that import binds to the release under
test; the pytest process binds the identical import statement to today's
checkout. The node source re-executed at verify time is therefore provably
the same text that produced the trace — the property the whole corpus exists
to check.

Keep every function module-level. ``Runtime`` fingerprints a node's identity
from ``inspect.getsource`` of the unwrapped callable (see
``_node_identity_parts`` in ``runtime.py``); a lambda or a nested ``def``
would change that fingerprint the moment it moved between the generation
process and the test process, for reasons that have nothing to do with the
property under test.
"""

from __future__ import annotations

from datetime import datetime, timezone

from vitruvyan_motus import Decision, EffectClass, EffectDescriptor, EffectReceipt, Fact

# Fixed across every scenario and every version. The corpus must regenerate
# byte-for-byte identically; a wall-clock timestamp would make every run of
# generate.py a diff even when nothing about Motus changed.
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


# --- pure_only: two pure nodes in sequence, no routing, no effects -----

def seed(state):
    return state.with_fact(Fact("n", 10, "seed", NOW))


def double(state):
    return state.with_fact(Fact("doubled", state.fact("n") * 2, "double", NOW))


# --- routed_decision: a pure node's Decision drives a "route" transition

def classify(state):
    return state.with_decision(Decision("branch", "big", NOW))


def big_path(state):
    return state.with_fact(Fact("label", "big", "big_path", NOW))


def small_path(state):
    return state.with_fact(Fact("label", "small", "small_path", NOW))


# --- recorded_effect: an effect with a receipt, then a pure reader -----

# A fixed receipt, not one minted per run — the corpus must regenerate
# byte-for-byte, and a fresh receipt id would defeat that on every run.
_RECEIPT = EffectReceipt(
    "provider:xv-corpus-1", result_fingerprint="effect:sha256:" + "b" * 64
)


def fetch(state, ctx):
    ctx.record_effect(EffectDescriptor(
        EffectClass.RECORDED_EFFECT, "GET xv-corpus fixture", receipt=_RECEIPT,
    ))
    return state.with_fact(Fact("payload", "fixture-data", "fetch", NOW))


def summarize(state):
    payload = state.fact("payload")
    return state.with_fact(Fact("summary", f"len={len(payload)}", "summarize", NOW))
