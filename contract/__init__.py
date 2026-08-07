"""The published contract, as the machine can read it.

This directory is the contract's authority (ADR-001): the prose in
``guarantees.md`` and ``node-protocol.md``, the two JSON Schemas, the frozen
fixtures, and ``validate.py`` — the program that decides whether a document
satisfies them.

The ``__init__`` exists so setuptools can map this directory into the
distribution as ``vitruvyan_motus.contract`` (see ``pyproject.toml``). Without
it the validator would stay on GitHub, which is where the first external
integration found it: their code documented the trace form as "the same bytes
``contract/validate.py`` accepts" and nobody ever ran it, because installing
Motus did not install it.

Only ``validate.py`` and the two schemas ship. The prose and the fixtures stay
in the repository — the reader who needs those is already reading it.
"""
