"""ADR-016 cross-check: a start that raised opens no session and promises no evidence."""
from vitruvyan_motus import Runtime
from vitruvyan_motus.graph import GraphSpec
from vitruvyan_motus.errors import NodeConfigurationError

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "g", "version": "1", "entry": "n",
    "nodes": [{"name": "n", "effect_class": "pure"}],
    "transitions": {"n": {"kind": "terminal"}},
})
calls = []
class Sink:
    def open_run(self, header):
        calls.append(header["run"]["run_id"])
        class S:
            def write(self, records): pass
            def finish(self, *, complete): calls.append(("finish", complete))
        return S()

rt = Runtime(SPEC, {"n": lambda s: s}, sink=Sink())
print("trace before anything :", rt.trace)
try:
    rt.run(run_id="")
except ValueError as e:
    print("refused               :", e)
print("sink calls after refusal:", calls)
print("runtime.trace           :", rt.trace, "  (ADR-008 §4: no run trace exists)")
r = rt.run(run_id="ok")
print("later run evidence      :", r.evidence, r.status)
print("sink calls              :", calls)
