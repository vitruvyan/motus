# TASK 017C — Regulatory Evidence Profile v1 adversarial closure

Parent: TASK 017 / ADR-038.

Input: 017A core implementation plus successful 017B mutation proof.

Adversarial lenses:
1. evidence-state escalation: can missing/not_verified/mismatched become matched?
2. artifact substitution: can an exact artifact be paired with contradictory
   Registry/Manifest/Receipt material and still match?
3. profile identity: can assessment findings come from a different profile
   revision than profile_fingerprint?
4. scope creep: did v1 accidentally become a selector/rules/compliance engine?
5. independence: can a non-Orbis Motus consumer validate and assess locally?

For every verified finding:
- reproduce;
- repair the class;
- regression test;
- mutation proof;
- rerun adversarial lens.

Closure gate:
- no unresolved verified finding;
- Jenkins green on exact final head;
- Jenkinsfile byte-identical to main;
- PR mergeable.
