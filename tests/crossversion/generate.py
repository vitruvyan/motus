"""Regenerate tests/crossversion's corpus from released Motus source trees.

Run from the repository root:

    .venv/bin/python tests/crossversion/generate.py

For each tag in VERSIONS this exports the release with ``git archive <tag> |
tar -x`` into a scratch directory — never ``git checkout``, which would
disturb the working tree — and runs every scenario in ``scenarios.py``
against that release's own ``Runtime``/``GraphSpec``/``Trace``, with a fixed
clock and identity so the output is byte-for-byte reproducible. The node
functions living in ``nodes.py`` are imported by both the archived release's
child process (which binds ``nodes.py``'s ``from vitruvyan_motus import
...`` to the release under test) and, later, by the test process (which
binds the same import to today's checkout) — see ``nodes.py``'s docstring
for why that is the point rather than an accident.

To add a newly released version: append its tag to VERSIONS below and rerun
this script. Do not hand-edit anything it writes under ``tests/crossversion/
v*/`` or ``manifest.json`` — regenerate instead, so the corpus stays exactly
what a real release would have produced. (The one sanctioned exception is
the deliberate, temporary corruption used to prove the test can fail — see
the top of test_crossversion_verify.py.)
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import scenarios

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_DIR = Path(__file__).resolve().parent

VERSIONS = ["v0.6.1", "v0.7.0"]


def _export_release(tag: str, dest: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", tag], cwd=REPO_ROOT, check=True, capture_output=True
    )
    subprocess.run(
        ["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True
    )


def _run_scenario(release_src: Path, scenario_name: str, run_id: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(CORPUS_DIR / "_generate_one.py"), scenario_name, run_id],
        env={"PYTHONPATH": str(release_src)},
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def main() -> None:
    manifest = []
    with tempfile.TemporaryDirectory(prefix="motus-xv-") as scratch:
        for version in VERSIONS:
            release_dir = Path(scratch) / version
            release_dir.mkdir()
            _export_release(version, release_dir)
            release_src = release_dir / "src"

            out_dir = CORPUS_DIR / version
            out_dir.mkdir(exist_ok=True)

            for scenario_name, (_, node_names) in scenarios.SCENARIOS.items():
                run_id = f"xv-{version}-{scenario_name}"
                produced = _run_scenario(release_src, scenario_name, run_id)

                trace_path = out_dir / f"{scenario_name}.trace.json"
                spec_path = out_dir / f"{scenario_name}.graphspec.json"
                trace_path.write_text(
                    json.dumps(produced["trace"], indent=2, sort_keys=True) + "\n"
                )
                spec_path.write_text(
                    json.dumps(produced["graph_spec"], indent=2, sort_keys=True) + "\n"
                )

                manifest.append({
                    "version": version,
                    "scenario": scenario_name,
                    "trace": f"{version}/{scenario_name}.trace.json",
                    "graph_spec": f"{version}/{scenario_name}.graphspec.json",
                    "nodes": node_names,
                })

                print(f"generated {version}/{scenario_name}")

    manifest_path = CORPUS_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {manifest_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
