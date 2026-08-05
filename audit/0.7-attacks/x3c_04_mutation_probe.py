"""x3c_04 — mutation probe: would the suite notice if the new fix vanished?

A fix with no failing test behind it is a fix that can be deleted by the next
refactor without anything going red. This neuters each of the round-three
changes in memory (the repository is not touched) and runs the full suite.

Any mutation that leaves the suite GREEN is a coverage gap.

Mutations:
  M1  `_is_async_node` always False  — the `node:<name>:async` constraint,
      i.e. the whole of the R2-F3 fix, stops being emitted.
  M2  `_iterator_is_finished` always True — restores the pre-fix latching that
      stranded a live run on a concurrency clash.
  M3  `_drive`'s `finally: machine.close()` removed — restores R2-F2.

Run: python .attack/x3c_04_mutation_probe.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PLUGIN = '''
import vitruvyan_motus.runtime as R
import vitruvyan_motus.observers as O

MUTATION = %r

if MUTATION == "M1":
    R._is_async_node = lambda node: False
elif MUTATION == "M2":
    O._iterator_is_finished = lambda iterator: True
elif MUTATION == "M3":
    import types

    def _drive(machine, invoke):
        reply = None
        while True:
            try:
                item = machine.send(reply)
            except StopIteration:
                return
            if type(item) is R._Invoke:
                try:
                    reply = invoke(item)
                except BaseException as exc:
                    machine.throw(exc)
                    raise
                continue
            reply = None
            yield item

    R._drive = _drive
'''

MUTATIONS = {
    "M1": "_is_async_node always False (the node:<name>:async constraint is gone)",
    "M2": "_iterator_is_finished always True (pre-fix latching restored)",
    "M3": "_drive's finally: machine.close() removed",
}


def run(mutation: str | None) -> tuple[int, str]:
    tmp = Path(tempfile.mkdtemp(prefix="x3c-mut-"))
    plugin = tmp / "x3c_mutate.py"
    plugin.write_text(PLUGIN % (mutation or "",))
    env = {
        **__import__("os").environ,
        "PYTHONPATH": f"{tmp}:{ROOT / 'src'}",
    }
    args = [sys.executable, "-m", "pytest", "tests/", "-q", "-x", "--no-header"]
    if mutation:
        args[3:3] = ["-p", "x3c_mutate"]
    proc = subprocess.run(args, capture_output=True, text=True,
                          cwd=str(ROOT), env=env, timeout=900)
    tail = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    return proc.returncode, (tail[-1] if tail else proc.stderr[-200:])


def main() -> int:
    code, summary = run(None)
    print(f"  baseline (no mutation)            exit={code}  {summary}")
    gaps = []
    for name, description in MUTATIONS.items():
        code, summary = run(name)
        verdict = "CAUGHT " if code != 0 else "GREEN !"
        if code == 0:
            gaps.append((name, description))
        print(f"  {name}: {description}")
        print(f"      -> {verdict} exit={code}  {summary}")
    print(f"\n{len(gaps)} mutation(s) the suite does not catch:")
    for name, description in gaps:
        print(f"  {name}: {description}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
