---
name: adversarial-round
description: Run a hostile review round against new Motus code, using several independent agents with different lenses, then process the findings. Use before merging anything substantial. This practice has found real defects in every round it has ever run.
---

# Running an adversarial round

## When a round is justified

**New code, or a named hypothesis. Not anxiety.** There is always something
else to find; rounds repeated without a target burn tokens and produce noise.
A round earns its cost when it attacks code written recently, or when several
independent reviewers converge on the same untested area.

## Shape

Dispatch **two or three `motus-adversary` agents in parallel, each with a
different lens** — they are independent, and convergence between them is the
strongest signal this project has produced. In the 0.7 cycle two agents found
the same buffered-teardown defect from a durability lens and a concurrency
lens, without knowledge of each other.

Lenses that have paid off: the new code itself; concurrency and contention;
durability and whether a persisted artifact is valid; the public protocol a
user would implement against.

Give each agent: the branch, the diff to read, the specific surfaces, the
contract files that are normative, and the constraint that it must try to
falsify a finding before reporting it.

## Do not merge while they are running

This is the rule that has mattered most. Green CI means "no existing test
broke", which says nothing about code no test covers yet. In the 0.7 cycle
every round found defects that would otherwise have landed — including in the
fixes made for the previous round.

## Processing findings

1. **Verify each one yourself** before acting. Agents are sometimes wrong, and
   twice in this project a finding evaporated under scrutiny.
2. **Fix, with a test that fails without the fix.**
3. **Mutation-probe every fix** with `tools/mutation_probe.py`, never by hand.
   Neuter the fix, re-run, confirm the suite notices. Five tests in the 0.7
   cycle passed for the wrong reason and only this caught them.

   **Use the tool and not three lines of shell, and this is not a style
   preference.** The obvious harness edits the file and restores it with
   `git checkout --`, which discards every uncommitted change to that file and
   not only the mutation. In this project it destroyed hours of unrelated work
   **four separate times**, each one after "commit before probing" had been
   written down and not kept. The tool restores from bytes held in memory and
   never touches git, so it is correct on a dirty tree, when the anchor is not
   found, and when pytest dies. It also refuses to start on uncommitted changes
   under `src/`, `contract/`, `tests/` and `benchmarks/`, for the one case
   in-memory restore cannot cover: the process being killed mid-probe.

   **A rule that has to be remembered is a rule that will be broken. Put it in
   the tool.** That is the general form, and it is the more useful half of this
   entry.

4. **A surviving mutant is not automatically a defect.** An *equivalent* mutant
   — one provably indistinguishable given the code around it — is a real
   category, and the honest response is to record why in the source so a later
   reader does not invent a contrived test to "cover" it. What is never
   acceptable is leaving a survivor unexplained.
5. **Re-run the agent's own scripts**, not just your new test. Twice the thing
   that caught a bad fix was the agent's script rather than mine.
6. **Report honestly**, including what did not reproduce and why. A finding
   that turns out to be a non-finding is information, not an embarrassment.

## Closing a round

Say what was found, what was fixed, what was left open **with a measurement
rather than an impression**, and what the next round should attack. Preserve
the attack scripts on an evidence branch — they reproduce findings the suite
cannot, and the next round starts from them instead of from nothing.
