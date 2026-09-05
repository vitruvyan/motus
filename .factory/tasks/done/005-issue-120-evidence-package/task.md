You are motus-lead. Checkout /home/vitruvyan/motus-factory (this cwd): `git fetch origin && git checkout -b
factory/120-evidence-package origin/main` first (main now carries #118, `CommitmentLog.receipt_for`). Read first:
AGENTS.md, GitHub issue #120, adr/ADR-027 (accepted), adr/ADR-023 (TraceBundle, the resumed run),
src/vitruvyan_motus/replay.py (`TraceBundle`: to_dict/to_json/explain/to_html), src/vitruvyan_motus/commitlog.py
(`receipt_for`), contract/validate.py (`verify(receipt, trace)`), demo/bundle_scenarios.py and demo/bundle_hiring.py
(the prototype this generalises), the demo's clean-directory verification (README section on `ots verify` and
demo/out/domains/*.root.txt).
Flow: architect (claude) → ONE implementer (pi luna) → verifier (pi luna) → TWO adversaries (claude), lenses:
(1) the second machine: verify from the package alone, then tamper each file one at a time; (2) the manifest
as a fifth identity in disguise — can anything in it be trusted that is not re-derived from the primitives.
Two correction rounds max. REPORT.md in .factory/tasks/ with MUTATION TARGETS. Never commit/push. Frozen dirs
untouched. No new runtime dependency (zipfile, json, hashlib are stdlib).

# TASK 005 — #120: the Evidence Package, an envelope that is not evidence

## Decisions (CTO; the architect refines the module layout, not these)
1. **No fifth identity.** The package has no fingerprint of its own. The manifest carries per-file sha256
   digests ONLY to catch a truncated or corrupted download; execution integrity is always re-derived from
   `trace.json` (root) and checked against `receipt.json` by `verify()`. The docstring and the manifest
   itself say so (`"integrity": "manifest digests detect transport damage; execution integrity is derived
   from trace.json, never from this file"`).
2. **Layout, fixed** (issue #120): a zip (deterministic: sorted names, fixed timestamps, no extra attributes)
   with `manifest.json`, `core/trace.json`, `core/graphspec.json`, `core/receipt.json` (from `receipt_for` when
   a log is given; absent, with the manifest saying "no commitment log: no receipt", when not), `core/proofs/`
   (anchor artefacts the caller hands over, e.g. `.ots` bytes, named by checkpoint digest), and
   `supplementary/attachments/` for material added later. `manifest.json`: `package_version: "1.0"`, the Motus
   version that packed it, `execution {ref, fingerprint, run_id}` copied from the receipt (or from
   `trace.root` + null ref without a log), the file list with digests and which section each is in, and
   `packed_at` (ISO-8601 UTC). Nothing in the manifest is used to decide validity.
3. **API, in `src/vitruvyan_motus/evidence.py`**: `pack(bundle: TraceBundle, *, log: CommitmentLog | None = None,
   anchors=(), proofs: Mapping[str, bytes] = {}, attachments: Mapping[str, bytes] = {}) -> bytes` and
   `verify_package(data: bytes) -> PackageVerdict` (a dataclass: the ADR-020 verdict from `verify()`, plus
   `transport_ok: bool`, and a list of `damaged` file names). `verify_package` must work with NOTHING but the
   bytes and the installed package: no filesystem paths, no environment, no network. CLI:
   `motus-validate package <file.zip>` prints the verdict the way `motus-validate receipt` does. Export both in
   `__all__` and the README's public surface (the verifier checks that list).
4. **Acceptance, brutal (issue #120)**: a test builds a package on one side, then, in a fresh `tmp_path` with
   only the bytes, verifies it: INTEGRITY ESTABLISHED. Then, one file at a time — trace, graphspec, receipt,
   manifest, a proof — a single byte is changed and the test asserts (a) transport damage is reported for the
   file whose digest moved, AND (b) for trace/receipt edits, the ADR-020 verdict drops for the right reason,
   independently of the manifest (a manifest re-computed by the attacker to match the edited trace must
   still fail on the receipt comparison). A package for an unfinished execution, and one for a resumed one,
   both verify with the notes `verify()` already produces. Zip-slip: a member name with `..` or an absolute
   path is refused before any byte is written anywhere (verify_package writes nothing anyway — say so).
5. Demo: `demo/bundle_scenarios.py` and `demo/bundle_hiring.py` call `pack()` instead of hand-assembling;
   `demo/out/` artefacts are NOT regenerated (they are frozen evidence of the prior format); the README's
   "verify from a clean directory" paragraph gains the one-line `motus-validate package` form.
6. Tests that fail without the code, frozen paths PASS, the SLO gate untouched (pack is not on the run path).
