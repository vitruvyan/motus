"""Same mutants, whole suite: does ANY test pin each half of the fix?"""
import pathlib, shutil, subprocess

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
    "M1-neutered-pre-fix": "                self._cancel_reason = None",
    "M2-has_started-only (drops the queued reason)": """                self._has_started = was_started
                self._cancel_reason = None""",
    "M3-pending-only": """                if not was_started:
                    self._pending_cancel_reason = self._cancel_reason
                self._cancel_reason = None""",
    "M4-blank-has_started": """                self._has_started = False
                self._pending_cancel_reason = self._cancel_reason
                self._cancel_reason = None""",
    "M5-restore-pending-unconditionally": """                self._has_started = was_started
                self._pending_cancel_reason = self._cancel_reason
                self._cancel_reason = None""",
}

def build(body):
    if MUT.exists():
        shutil.rmtree(MUT)
    MUT.mkdir(parents=True)
    shutil.copytree(SRC, MUT / "vitruvyan_motus")
    p = MUT / "vitruvyan_motus" / "runtime.py"
    t = p.read_text()
    assert ORIGINAL in t
    p.write_text(t.replace(ORIGINAL, body))

for name, body in VARIANTS.items():
    build(body)
    r = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "tests", "-q", "--no-header",
         "-p", "no:cacheprovider", "-x", "--tb=line"],
        cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": str(MUT), "PATH": "/usr/bin:/bin", "HOME": "/home/vitruvyan"},
    )
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    print(f"{name:48s} -> {lines[-1][:120]}")
