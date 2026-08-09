"""Run one corpus scenario and print its trace + graph spec as one JSON line.

Invoked by ``generate.py`` as a child process with an archived release
placed first on ``PYTHONPATH``: ``import vitruvyan_motus`` here binds to that
release, not to whatever is installed in the calling environment. Kept as a
separate process, not an in-process import, so a defect in an old release
(an infinite loop, a segfault-equivalent) cannot take the generator with it,
and so two releases' modules — which do not agree on much beyond the public
surface — are never on ``sys.path`` at once.

Usage: python _generate_one.py <scenario-name> <run-id>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nodes  # noqa: E402  (path insert above must run first)
import scenarios  # noqa: E402

from vitruvyan_motus import GraphSpec, Runtime  # noqa: E402


def main() -> None:
    scenario_name, run_id = sys.argv[1], sys.argv[2]
    spec_dict, node_names = scenarios.SCENARIOS[scenario_name]
    spec = GraphSpec.from_dict(spec_dict)
    registry = {name: getattr(nodes, fn) for name, fn in node_names.items()}

    def clock():
        return nodes.NOW

    def identity():
        return run_id

    result = Runtime(spec, registry, clock=clock, identity=identity).run()
    json.dump(
        {"trace": result.trace.to_dict(), "graph_spec": spec.to_dict()},
        sys.stdout,
    )


if __name__ == "__main__":
    main()
