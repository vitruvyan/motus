"""Mutation probe harness for commit 4010210.

Rewrites the `except BaseException` rollback in a shadow copy of the package
and runs the two new tests against each variant.
"""
import pathlib, re, shutil, subprocess, sys

ROOT = pathlib.Path("/home/vitruvyan/motus")
MUT = ROOT / ".attack" / "mut"
SRC = ROOT / "src" / "vitruvyan_motus"

ORIGINAL = """                self._has_started = was_started
                if not was_started:
                    # Whatever bound during this window never took effect: the
                    # reason queued before the call, or one a concurrent caller
                    # lodged against the run while `_running` was up.
                    self._pending_cancel_reason = self._cancel_reason
                self._cancel_reason = None"""

VARIANTS = {
    "M0-as-shipped": ORIGINAL,
    "M1-neutered-pre-fix": """                self._cancel_reason = None""",
    "M2-has_started-only": """                self._has_started = was_started
                self._cancel_reason = None""",
    "M3-pending-only": """                if not was_started:
                    self._pending_cancel_reason = self._cancel_reason
                self._cancel_reason = None""",
    "M4-blank-has_started": """                self._has_started = False
                self._pending_cancel_reason = self._cancel_reason
                self._cancel_reason = None""",
}

TESTS = [
    "tests/test_motus_runtime.py::test_a_queued_cancellation_survives_a_start_that_never_began_a_run",
    "tests/test_motus_runtime.py::test_a_failed_start_does_not_reopen_the_queue_on_a_used_runtime",
]

def build(body: str) -> None:
    if MUT.exists():
        shutil.rmtree(MUT)
    MUT.mkdir(parents=True)
    shutil.copytree(SRC, MUT / "vitruvyan_motus")
    p = MUT / "vitruvyan_motus" / "runtime.py"
    text = p.read_text()
    assert ORIGINAL in text, "anchor not found"
    p.write_text(text.replace(ORIGINAL, body))

for name, body in VARIANTS.items():
    build(body)
    print(f"--- {name} ---")
    for t in TESTS:
        r = subprocess.run(
            [str(ROOT / ".venv/bin/python"), "-m", "pytest", t, "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True,
            env={"PYTHONPATH": str(MUT), "PATH": "/usr/bin:/bin", "HOME": "/home/vitruvyan"},
        )
        tail = [l for l in r.stdout.splitlines() if l.strip()][-1]
        short = t.split("::")[1]
        print(f"  {'PASS' if r.returncode == 0 else 'FAIL'}  {short}")
        if r.returncode != 0:
            for line in r.stdout.splitlines():
                if line.startswith("E ") or ">       assert" in line or line.startswith("tests/"):
                    print("        " + line.rstrip())
