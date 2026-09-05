"""The standalone root reader in the hiring bundle follows current traces."""

from __future__ import annotations

import json

from vitruvyan_motus import GraphSpec, Runtime, State

from demo.bundle_hiring import DERIVE


def test_standalone_hiring_reader_derives_a_current_trace_root():
    namespace: dict[str, object] = {"__name__": "demo_bundle_derive_test"}
    exec(DERIVE, namespace)
    derived_root = namespace["derived_root"]

    spec = GraphSpec.from_dict({
        "schema_version": "1.0.0",
        "name": "hiring-reader-test",
        "version": "1.0.0",
        "entry": "review",
        "nodes": [{"name": "review"}],
        "transitions": {"review": {"kind": "terminal"}},
    })
    trace = Runtime(spec, {"review": lambda state: state}).run(
        State.empty(), run_id="hiring-reader-current"
    ).trace
    document = json.loads(trace.to_json())

    root, reason = derived_root(document)
    assert reason is None
    assert root == trace.root
