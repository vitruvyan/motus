"""Attack F: durability profiles under real process termination.

Each scenario runs in a child process that is killed with os._exit at a
controlled record, then the parent inspects what survived on disk and compares
it with the profile the run header declared.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHILD = r'''
import json, os, sys
from datetime import datetime, timezone
from vitruvyan_motus import Fact, GraphSpec, Runtime, State

profile, kill_at, path = sys.argv[1], sys.argv[2], sys.argv[3]
NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "kill", "version": "1.0.0", "entry": "n0",
    "nodes": [{"name": f"n{i}", "effect_class": "pure"} for i in range(6)],
    "transitions": {f"n{i}": ({"kind": "terminal"} if i == 5
                              else {"kind": "next", "to": f"n{i+1}"})
                    for i in range(6)},
})

class RunSink:
    def __init__(self, path):
        self.path = path
    def write(self, records):
        with open(self.path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

class Sink:
    def __init__(self, path):
        self.path = path
    def open_run(self, header):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"header": header}) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return RunSink(self.path)

class Killer:
    def __init__(self, kill_at):
        self.kill_at, self.seen = kill_at, 0
    def on_record(self, record):
        self.seen += 1
        if f"{record['kind']}#{record['seq']}" == self.kill_at or record["kind"] == self.kill_at:
            sys.stdout.flush()
            os._exit(9)

def node(state):
    return state.with_fact(Fact("k", 1, "s", NOW))

runtime = Runtime(SPEC, {f"n{i}": node for i in range(6)},
                  durability_profile=profile, sink=Sink(path),
                  listeners=(Killer(kill_at),),
                  chunk_records=1000, flush_interval_ms=600000)
result = runtime.run(State.empty("kill"))
print("SURVIVED", result.status)
'''


def run_child(profile, kill_at, path):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run([sys.executable, "-c", CHILD, profile, kill_at, str(path)],
                          capture_output=True, text=True, env=env, cwd=str(ROOT))
    return proc


print("=== process killed mid-run; what did the declared profile actually buy? ===")
with tempfile.TemporaryDirectory() as tmp:
    for profile in ("synchronous", "buffered"):
        for kill_at in ("routing", "run_completed", "none"):
            path = Path(tmp) / f"{profile}-{kill_at}.jsonl"
            proc = run_child(profile, kill_at, path)
            lines = path.read_text().splitlines() if path.exists() else []
            kinds = []
            header_seen = False
            for line in lines:
                obj = json.loads(line)
                if "header" in obj:
                    header_seen = True
                else:
                    kinds.append(obj["kind"])
            print(f"   {profile:12} kill_at={kill_at:13} rc={proc.returncode:3} "
                  f"header={header_seen} persisted={len(kinds):2} {kinds}")
            if proc.returncode not in (0, 9):
                print("      stderr:", proc.stderr.strip()[-200:])

print("\n=== the declared loss window vs what buffered really loses ===")
print("   guarantees.md: buffered is durable up to the last confirmed flush;")
print("   failed-transition and terminal records flush immediately.")

print("\n=== a sink that mutates the records it is handed ===")
sys.path.insert(0, str(ROOT / "src"))
from datetime import datetime, timezone  # noqa: E402
from vitruvyan_motus import Fact, GraphSpec, Runtime, State  # noqa: E402

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "m", "version": "1.0.0", "entry": "a",
    "nodes": [{"name": "a", "effect_class": "pure"}],
    "transitions": {"a": {"kind": "terminal"}},
})


class MutatingRunSink:
    def __init__(self):
        self.seen = []

    def write(self, records):
        for record in records:
            record["kind"] = "FORGED"
            record["seq"] = 999
            if "writes" in record:
                record["writes"]["facts"] = [{"key": "evil", "value": 1, "source": "x", "ts": "2026-01-01T00:00:00Z"}]
            self.seen.append(record)


class MutatingSink:
    def __init__(self):
        self.run_sink = MutatingRunSink()

    def open_run(self, header):
        header["run_id"] = "HIJACKED"
        return self.run_sink


sink = MutatingSink()
out = Runtime(SPEC, {"a": lambda s: s.with_fact(Fact("k", 1, "s", NOW))},
              durability_profile="synchronous", sink=sink).run(State.empty("m"))
print("   run_id in trace   :", out.trace.run["run_id"][:8], "... (not HIJACKED:",
      out.trace.run["run_id"] != "HIJACKED", ")")
print("   trace record kinds:", [r["kind"] for r in out.trace.records])
print("   committed fact k  :", out.state.fact("k"), "| evil present:", out.state.fact("evil"))

print("\n=== a listener that mutates the record it is handed ===")


class MutatingListener:
    def on_record(self, record):
        record["kind"] = "FORGED"
        record.clear() if isinstance(record, dict) else None


out = Runtime(SPEC, {"a": lambda s: s.with_fact(Fact("k", 1, "s", NOW))},
              listeners=(MutatingListener(),)).run(State.empty("m"))
print("   trace record kinds:", [r["kind"] for r in out.trace.records])

print("\n=== run_id containing traversal / separators ===")
for run_id in ("../../etc/passwd", "a\\b:c", "\x00null", "x" * 201, ""):
    try:
        out = Runtime(SPEC, {"a": lambda s: s}).run(State.empty("m"), run_id=run_id)
        print(f"   run_id {run_id[:24]!r:28} accepted -> header {out.trace.run['run_id'][:24]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"   run_id {run_id[:24]!r:28} refused  -> {type(exc).__name__}: {exc}")

print("\n=== compat FileTraceObserver path traversal (inherited corpus item 4) ===")
from vitruvyan_motus.compat import FileTraceObserver, GraphState  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    observer = FileTraceObserver(tmp)
    for trace_id in ("../escape", "/abs/path", "a/b/c", "..\\win"):
        state = GraphState.empty(trace_id)
        observer.observe("graph_end", state)
    produced = sorted(p.relative_to(tmp).as_posix() for p in Path(tmp).rglob("*") if p.is_file())
    escaped = sorted(p.name for p in Path(tmp).parent.glob("*escape*"))
    print("   files inside dir :", produced)
    print("   files outside dir:", escaped)
