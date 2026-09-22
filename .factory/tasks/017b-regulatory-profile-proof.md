# TASK 017B — Regulatory Evidence Profile v1 mutation proof

Parent: TASK 017 / ADR-038.

Input: exact committed 017A tree.

Goal: prove the focused tests detect removal/weakening of the load-bearing
0.17 invariants using tools/mutation_probe.py and
.factory/probes/regulatory-evidence-profile-v1.json.

Rules:
- no product changes merely to make the mutation runner work;
- no temporary mutation code may remain in the branch;
- the normal Jenkinsfile is not product evidence and must be restored after any
  temporary proof stage;
- record each probe as KILLED or SURVIVED;
- any SURVIVED probe blocks closure until explained as equivalent or fixed.

Output: mutation-proof evidence only.
