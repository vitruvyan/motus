"""The demo runs, and says what it is supposed to say.

A demo nobody executes rots the way an example does, and this one makes a
claim about the product: that the anchored root catches an editor the
validator cannot. If that stops being true the demo must fail, not mislead.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO = REPO_ROOT / "demo" / "attack_this_run.py"


def test_the_demo_runs_and_writes_its_page(tmp_path):
    page = tmp_path / "out.html"
    done = subprocess.run(
        [sys.executable, str(DEMO), "--html", str(page)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert done.returncode == 0, done.stderr
    assert page.exists() and page.stat().st_size > 4000
    assert "<!doctype html>" in page.read_text(encoding="utf-8")


def test_every_tamper_is_caught_on_the_trace_and_only_the_anchor_catches_the_last():
    """The demo's whole argument, asserted rather than illustrated."""
    sys.path.insert(0, str(REPO_ROOT / "demo"))
    from attack_this_run import _who_caught, run_demo

    data = run_demo()
    assert data["root"] is not None, "a completed 3.0.0 run must have a root"

    verdicts = [_who_caught(r)[0] for r in data["results"]]
    assert "nessuno" not in verdicts, (
        f"a tamper went unnoticed on the trace: {verdicts}"
    )
    assert verdicts[-1] == "ancora", (
        "the resealing editor was caught by the validator, which means either "
        "the reseal is not faithful or the validator gained a check it cannot "
        "have — a resealed trace is internally consistent by construction"
    )
    assert verdicts[:-1] == ["validatore"] * (len(verdicts) - 1)

    # And the log is genuinely weaker, for the reason the demo states: it has
    # per-row digests, so it catches payload edits and nothing structural.
    caught = [r["log_caught"] for r in data["results"]]
    assert caught.count(True) == 3, caught
