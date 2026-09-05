---
description: Run a hostile review round against new Motus code — two or three adversaries with different lenses, then process the findings the Motus way.
---

Run an adversarial round on the change named below. Follow `.pi/skills/adversarial-round/SKILL.md` exactly;
it is the same practice that found real defects in every round it has ever run.

1. Read `contract/guarantees.md`, the ADRs the change touches, and the diff (`git diff <base>...HEAD` or the
   branch named below). Restate in three lines what the change claims.
2. Spawn **two or three** `motus-adversary` agents in parallel (under pi-herdr: agent `claude`, one pane each),
   each with a different lens, chosen in this order: (1) the new code itself, always; (2) then, of
   concurrency/contention, durability of the persisted artifact, and the public protocol, the one or two the
   change touches most; drop the rest and say which you dropped in REPORT.md. Each must try to falsify
   a finding before reporting it, and must save its attack scripts under `.attack/`.
3. Verify every finding yourself before acting. Name the class before the fix ("what else has this shape?"),
   and sweep for it.
4. For each confirmed finding: a fix with a test that fails without it, then `tools/mutation_probe.py` on the
   fix — never a hand-made probe. A surviving mutant is either explained in the source as equivalent, or a defect.
5. Re-run the adversaries' own scripts, not only the new tests.
6. REPORT.md: what was found, what was fixed, what stays open **with a measurement**, and what the next
   round should attack. No commit, no push.

Change to attack:
