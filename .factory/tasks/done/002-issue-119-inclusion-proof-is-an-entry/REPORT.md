# Report — #119

## RESULT
`InclusionProof.to_dict()` now emits exactly the receipt.v1 `$defs.Entry`
shape. The old `path`, `format`, `mode`, and `checkpoint_digest` dictionary
members are gone; the latter three remain available as properties on the
proof object.

## ARTIFACTS
- `src/vitruvyan_motus/commitlog.py`
- `tests/test_verifier.py`
- `tests/test_commitlog.py`
- `tests/test_commit_lifecycle.py`
- `tests/test_contract_commitments.py`
- `.attack/119/probe1.py`, `.attack/119/probe2.py`

The verifier fixture now obtains segment entries directly from
`proof.to_dict()`. Schema coverage exercises one-leaf and multi-leaf windows,
and receipt CLI acceptance uses generated entries.

## TESTS (raw output)
Verifier:
```
.venv/bin/pytest -q
1120 passed, 5 skipped in 59.59s

.venv/bin/python tools/check_frozen_paths.py demo/three-domains-anchored HEAD
Frozen contract paths: PASS
```

Required old-key grep:
```
grep -rn '"path"' src/ demo/ tests/
src/vitruvyan_motus/sinks.py:63: __slots__ = ("path", ...)
src/vitruvyan_motus/mcp/__main__.py:63: diagnose.add_argument("path", ...)
[remaining tests hits are unrelated path parameters/data]
```
No InclusionProof receipt consumer uses the old `path` key.

Adversarial probes passed:
- `.attack/119/probe1.py`: properties, witnessed/one-leaf output, schema.
- `.attack/119/probe2.py`: multi-leaf subprocess `motus-validate receipt`
  acceptance, exit 0.

## MUTATION CHECKS
The implementer restored the old `path`/metadata output temporarily; the new
schema and receipt tests failed, then the fixed source was restored. No
mutation was left in the tree. The CTO runs `tools/mutation_probe.py` after
committing; it refuses uncommitted trees, so it was not run here.

## MUTATION TARGETS
The CTO mutation probe should neuter these load-bearing regions:

- `src/vitruvyan_motus/commitlog.py:174-187`: `.format`, `.mode`, and
  `.checkpoint_digest` properties.
- `src/vitruvyan_motus/commitlog.py:189-204`: `to_dict()`'s exact Entry keys,
  `path` to `{side, digest}` conversion, checkpoint, and conditional witness.
- `tests/test_verifier.py:63-65`: the real fixture's use of
  `proof.to_dict()`; replacing it with hand-built entries must be detected by
  the schema/CLI integration coverage.

## FINDINGS
No confirmed adversarial findings. The public-protocol adversary verified that
properties preserve the former information, `to_dict()` has no extra keys and
validates for empty and non-empty proofs, witnessed entries validate, the CLI
accepts generated entries, and no old receipt-path consumer remains.

## OUT OF SCOPE, NOTICED
The contract schema was not changed. The internal dataclass field remains
named `path`; only the public serialized Entry vocabulary changes.

## AGENTS
One implementer (`pi`, `openai-codex/gpt-5.6-luna`) made the implementation and
tests; one verifier (`pi`, same model) ran the full suite and frozen-path guard;
one public-protocol adversary (`claude`) ran the independent probes. No agent
committed, pushed, tagged, or released.
