# TASK 023 — Third-party adapter profile and conformance kit v1

Parent: ADR-044, proposed on 2026-09-27. The founder has not accepted it.

Branch: `feat/third-party-adapter-profile-v1`.

Target release: to be decided after ADR acceptance. This integration milestone
does not automatically claim or consume a core release number.

## Gate

Stop after this proposed ADR until the founder accepts, rejects or amends
ADR-044. Do not add a contract, fixtures, helper, public API or roadmap
completion claim while it remains `PROPOSED`.

## Goal after acceptance

Ship the smallest transport-neutral profile and conformance kit that let a
third-party stack expose existing Motus receipt, package and ADR-043 operations
without duplicating verification semantics.

## Expected bounded surface

- one normative adapter-profile document;
- one versioned neutral conformance corpus;
- one caller-supplied invocation hook and standard-library conformance runner;
- one in-process reference adapter example;
- a documented Orbis mapping using the already qualified bridge;
- focused preservation, failure-separation, execution-ref, version and bounds
  tests;
- packaging, mutation, independent, adversarial and Jenkins evidence.

## Stop conditions

Return to ADR review if implementation needs a network framework, authentication
model, tenant-role vocabulary, storage schema, hidden discovery, a new evidence
kind, verifier duplication, result simplification, product vocabulary, runtime
dependency, generated language client, or a change to an existing artifact or
result contract.

## Definition of done

1. ADR-044 is founder-accepted with any amendments recorded;
2. the profile names only existing public Motus semantics;
3. retrieval failures and verification results remain distinguishable;
4. exact package bytes and structured result fields survive the adapter;
5. execution operations use BEGIN-located ADR-027 references;
6. unsupported profile/interface majors fail closed;
7. conformance fixtures cover positive, negative and hostile cases;
8. the runner imports no host framework and adds no runtime dependency;
9. Orbis maps to the profile without moving Orbis auth/storage into Motus;
10. focused and full tests, packaging, frozen-path guards and mutation probes
    pass;
11. independent and adversarial review have no unresolved finding;
12. Jenkins is green on the exact reviewed head and on `main` after merge;
13. the roadmap and, only after the implementation is stable, the Motus pages
    in `vitruvyan-docs` describe the shipped boundary without calling it a
    compliance SDK.
