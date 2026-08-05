# 0.7 adversarial evidence

The scripts six independent adversarial rounds used against Motus during the
0.7 cycle. They are preserved because a finding is only as good as the thing
that reproduces it, and because several of them caught defects that the test
suite — including tests written specifically for those defects — did not.

They are **not** part of the test suite and are not run by CI. Several are
deliberately destructive, several take minutes, and several only reproduce
probabilistically. Their value is as evidence and as a starting point for the
next round.

## The rounds

| prefix | round | target |
|---|---|---|
| `x1_*`, `x1b_*`, `x1c_*` | 1–3 | the asynchronous surface, as it was being built |
| `x2_*`, `x2b_*`, `x2c_*` | 1–3 | driver lifecycle, cancellation, cross-driver leakage |
| `x3_*`, `x3b_*`, `x3c_*` | 1–3 | replay, conformance, version coupling, mutation probing |
| `x4a_*` | 4 | `_ObservationHub` under contention, listener re-entry |
| `x4b_*` | 4 | the run-handle machinery and the abandoned-driver finaliser |
| `x4c_*` | 4 | durability, sink semantics, artifact validity |
| `x5_*` | 5 | the sink protocol change (TraceHeader, `finish`) |
| `x6_*` | 6 | `JsonlTraceSink`, the shipped durable sink |

## Running them

Most take a repo checkout and the package installed:

```console
python -m venv .venv
.venv/bin/pip install -e ".[test]" -c constraints/test.txt
cd audit/0.7-attacks && PYTHONPATH=. ../../.venv/bin/python x4c_06_terminal_retry_duplicate.py
```

Some need `X4C_OUT` set to a writable directory for the artifacts they produce.
Several import a sibling `*_common.py`, which is why `PYTHONPATH=.` is needed.

## What they found

Across the six rounds: an artifact that asserted both that a run succeeded and
that it failed at the same `seq`; a buffered profile that discarded 47 records
while the process was alive; traces delivered to the wrong caller across
threads; a `weakref` finaliser that leaked every Runtime it touched; a `close()`
that executed the graph instead of cancelling it; a session that was opened and
then never told anything; and one ADR that made a false claim about itself.

`x3c_04_mutation_probe.py` is the mutation prober — it neuters each fix in
memory and re-runs the suite, on the principle that a fix no test can detect is
not a fix. It caught five tests that passed for the wrong reason.
