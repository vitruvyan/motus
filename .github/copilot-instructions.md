# Vitruvyan Motus — implementation instructions

This repository is the continuous history of Axis, now governed as
**Vitruvyan Motus**. Axis v0.4.0 is the predecessor and compatibility source;
Motus 0.5 is a trace-first graph runtime implemented inside an accepted
contract.

## Authority order

1. `adr/ADR-001-trace-first-single-semantics.md`
2. the four surfaces in `contract/`
3. `tests/contract/` and `tests/compat/terraveler/`
4. implementation code

When implementation and contract disagree, implementation is wrong. A needed
contract change is a prior, versioned amendment with its own ADR.

## Frozen evidence

- Do not edit anything under `tests/contract/` or `tests/compat/`.
- The sole exception is `tests/contract/kernel.py`, which changes once when
  imports move from `axis` to `vitruvyan_motus.compat`.
- Never weaken, skip, delete, or rewrite a failing contract or compatibility
  assertion to make an implementation pass.
- CI enforces these paths from the trusted base branch.

## Runtime boundary

- Distribution: `vitruvyan-motus`; package: `vitruvyan_motus`.
- One execution semantics. No co-equal interpreted and compiled engines.
- The kernel is domain-neutral: no LLM, LangChain, Orders, Vitruvyan OS, or
  epistemic claims.
- Native and legacy decisions are distinct public types. Legacy behavior lives
  only in `vitruvyan_motus.compat`; compatibility code adds no new semantics.
- Trace causality, declared nondeterminism, durability profiles, structural
  redaction, and the GraphSpec/trace schemas are contract, not suggestions.
- Keep the approved package flat and lean. Do not create `core`, `common`, or
  `utils` dumping grounds.

## Verification

Use the pinned environment and run all gates:

```console
python -m pip install -e ".[test]" -c constraints/test.txt
python -m pytest tests/ -q
python benchmarks/check_slo_baseline.py
```

Performance measurements from a new runner class are not comparable evidence
until that profile has at least five published runs under guarantees.md §3.
