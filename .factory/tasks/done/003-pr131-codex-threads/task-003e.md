You are motus-lead. TASK 003e — three Codex P2 threads on PR #134, all verified true by the CTO. ONE implementer
(pi luna) → verifier (pi luna) → ONE adversary (claude) on these three changes only. Never commit/push. Append
"## 003e" to .factory/tasks/done/003-pr131-codex-threads/REPORT.md, add probes for each to the MUTATION TARGETS
list, and stop.

1. demo/anchor_domains.py:67 — `commit()` builds `proof_file`/`verify_with` from `out.name`, which drops parent
   directories: called from demo/anchor_scenarios.py with `out = demo/out/scenarios/<s>`, the files are written
   there while the receipt advertises `demo/out/<s>/…`, a file that does not exist. Derive the advertised path
   relative to the repository root (the parent of the `demo` directory), for every depth. Test: a nested `out`
   two levels under `demo/out` advertises the path the file was actually written to, and `Path(advertised)`
   resolved against the repo root exists after the call.

2. plug __init__.py ~:304 — when pass MAX_UPGRADE_PASSES merges successfully and exposes a new pending child,
   `last_request` names the PARENT that just answered, so the ceiling record blames a request that succeeded and
   omits the one left unattempted. On hitting the ceiling, record every pending (calendar, commitment) still in
   the tree that is NOT in `attempted` — those are the requests the upgrade did not make — and never a key that
   was answered. Test: a growing calendar; at the ceiling the outage entries are exactly the unattempted
   children, none of them a key that was merged.

3. plug __init__.py ~:293 — failures use `unreachable.setdefault(key, …)`, and the dict is preloaded from the
   previous receipt, so a request that fails again for a different reason keeps the OLD diagnostic (a timeout,
   then an authorization error → the receipt still says timeout). Assign, do not setdefault, on both failure
   sites (unknown calendar, exception); keep the success-path pop. Test: two upgrades, the same request fails
   with two different exceptions; the second receipt carries the second reason.

Verifier: full suite, plug suite, frozen-path guard, .attack/pr131/*.py. Adversary lens: after 2, can a
ceiling record ever name a key in `attempted`? After 1, run the real demo modules' path logic for
demo/out/domains, demo/out/scenarios/<s> and a hypothetical three-level directory. After 3, is there any other
site that preloads state from the previous receipt and lets it win over this upgrade's answer (heights?
serialized? upgraded_at?).
