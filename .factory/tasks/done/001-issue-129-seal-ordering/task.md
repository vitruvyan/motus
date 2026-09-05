You are the lead of the Motus factory session. Checkout: /home/vitruvyan/motus-factory (this cwd), a git
worktree on branch `fix/129-seal-ordering` from `demo/three-domains-anchored` (which carries `dd2140a`, the
`_append` fix this issue builds on). Read first: `AGENTS.md` (all of it — the rules carry the incidents that
made them), `contract/guarantees.md`, `src/vitruvyan_motus/commitlog.py` lines 1–40 (the module's opening
principle) and GitHub issue #129 (`gh issue view 129`). Python: `.venv/bin/python`, `.venv/bin/pytest`.
Never commit, push, tag or release. `tests/compat/` and `tests/contract/` are frozen: never add to or edit them.

Flow, in this order and nothing else:
1. architect (agent "claude"): reads the issue and `commitlog.py`, writes PLAN.md with the exact ordering
   change at each site and the sweep list from the issue's point 4;
2. ONE implementer (agent "pi", agentArgs ["--model","openai-codex/gpt-5.6-luna"]);
3. verifier (agent "pi", same agentArgs): `.venv/bin/pytest -q` full suite and
   `.venv/bin/python tools/check_frozen_paths.py`, raw output, anomalies only;
4. **adversarial round**: TWO adversaries (agent "claude", one pane each), lenses: (a) durability — is the
   persisted artifact valid after every failure you can inject at every durable write in `commitlog.py`;
   (b) the class — "a successful durable write followed by an unguarded failure": find any site the fix
   missed, including `_write_identity` and `_replay`'s truncate. Each must try to falsify a finding before
   reporting it and save attack scripts under `.attack/129/`;
5. confirmed findings go back to the implementer; then verifier again; two rounds maximum;
6. REPORT.md per `~/.pi/agent/AGENTS.md`, plus a section MUTATION TARGETS: the lines of the fix a mutation
   probe should neuter (the CTO runs `tools/mutation_probe.py` after committing; it refuses uncommitted trees,
   so it is not your step — say so, do not run it by hand).

# TASK 001 — #129: a failed seal() must not leave a chain no process can open again

## What is owed (from the issue, verbatim in intent)
1. `seal()` writes the checkpoint durably **before** mutating `_window`, or poisons the store on failure —
   the same ordering `_append` already has since `dd2140a`.
2. `_append`'s `self._window.append(commitment)` moves inside the poison guard, so a failure after a durable
   write cannot leave the in-memory sequence unadvanced while the disk advanced.
3. A test for a **failed** `seal()`: inject an `OSError` at the checkpoint write and assert (a) the store is
   poisoned or the seal is retryable, never "this window is sealed" with nothing on disk; (b) two subsequent
   `begin()`/`end()` calls do not produce a window file that `CommitmentLog.__init__` refuses with
   `CommitmentLogFork`; (c) after a restart the chain reopens.
4. The sweep as a property, not a site: for every function in `commitlog.py` that performs a durable write,
   what can raise after it, and is the store poisoned? Write the answer as a table in PLAN.md and turn it into
   one parametrised test where a fault is injected after each durable write.
5. The second consequence: a `begin()` that raised must not leave a durable BEGIN that a later open absorbs and
   seals as if the run had started (ADR-020 decision 3, ADR-021 decision 8). Either the BEGIN is not durable
   until `begin()` returns, or the failure poisons. Test it.

## Constraints
- Only `src/vitruvyan_motus/commitlog.py`, `src/vitruvyan_motus/commitments.py` if the window's `seal()`
  must change, and `tests/test_commitlog.py` (add tests; do not weaken existing ones).
- No contract change. If you conclude the contract must change, stop: that needs an ADR the founder accepts.
- Every new test must fail without the fix; the report says how you checked (revert the fix in a temp copy,
  run the test, restore).

## Acceptance
Full suite green; frozen-path guard green; both adversaries' scripts under `.attack/129/` re-run green;
REPORT.md with the sweep table and the MUTATION TARGETS section.
