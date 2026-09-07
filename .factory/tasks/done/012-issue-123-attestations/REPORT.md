# TASK 012 — #123, ADR-031: an anchor is a chain, an attestation is a party

Written by the CTO session from the finishing implementer's report (subagents cannot write under `.factory/`).

## RESULT

Delivered. `receipt.v1` gains the optional `attestations[]` container with `$defs.Attestation` (seven required
fields, `proof` typed per type by `if/then`); `contract/validate.py` gains `KNOWN_ATTESTATION_TYPES`
(`rfc3161_timestamp` only), rules P9 and P10 (unknown type, algorithm the type does not admit, missing proof
keys — violation AND refusal, the P5 pattern), and the EXISTENCE verdict scoped by the subject (ADR-031
decision 7, review correction 1: CLAIMED only when the subject is the run root or a checkpoint whose range
includes the END; a BEGIN-only subject reports existence of the BEGIN and EXISTENCE NOT ESTABLISHED);
`commitments.py` gains the `Attestation` dataclass (validated through `_SCHEMA_FIELDS`, strict plain JSON
proof) and the `Attester` protocol; `commitlog.receipt_for(..., attestations=)` carries them; the evidence
package walks them like anchors. Six fixtures (311–316). The plug `plugs/motus-attest-rfc3161` builds the DER
`TimeStampReq`, reads `genTime` and the TSA name from the `TimeStampResp` with a bounded stdlib DER walker,
and produces the `Attestation` — **ADR-031 hypothesis H1 holds**: no dependency, and a real public TSA
(freetsa.org) parsed live. H1's line-count guess (200) was wrong (438 lines); not a falsification condition.

## ARTIFACTS

- `contract/receipt.v1.schema.json` (+51), `contract/validate.py` (+221/−34: `KNOWN_ATTESTATION_TYPES`,
  `_ATTESTATION_TYPE_RULES`, P9, P10, refusal echo, `_covers_execution` shared by anchors and attestations),
  `contract/README.md` (+38/−6: rows P9/P10, one refusal, «what an attestation is not»).
- `src/vitruvyan_motus/commitments.py` (+78: `Attestation`, `Attester`), `commitlog.py` (+8/−1),
  `evidence.py` (+5/−1).
- `tests/test_attestations.py` (431 lines, 22 tests incl. the four-row table that fails on a fifth member),
  `tests/test_commitments_schema_fields.py`, `tests/test_stand_ins.py`, `tests/test_contract_fixtures.py`.
- `contract/fixtures/311-receipt-attestation-rfc3161-claimed.json`, `312-…-p9-subject-is-not-a-checkpoint-or-root`,
  `313-…-p10-unknown-type`, `314-…-p10-algorithm-not-admitted`, `315-…-proof-missing-token-der`,
  `316-…-attestation-over-the-begins-checkpoint`.
- `plugs/motus-attest-rfc3161/` — `src/motus_attest_rfc3161/__init__.py` (438 lines: `_tlv`, `_children`,
  `_parse_timestamp_resp`, `_tsa_name`, `build_timestamp_request`, `Rfc3161Attester`), `tests/test_attester.py`
  (456 lines, 36 tests incl. the opt-in live test), `tests/fixtures/response.tsr` (a genuine `TimeStampResp`
  signed by a throwaway TSA with `openssl ts -reply` over a query built by the plug itself), `pyproject.toml`
  (zero runtime deps; `jsonschema` in test extras), `README.md`. `.github/workflows/ci.yml` (+6: the
  `anchor-plugs` job installs and tests this plug — PLAN-123 item 11 had been skipped).

## TESTS (raw)

```
$ .venv/bin/pytest tests/ plugs/motus-attest-rfc3161/tests -q
1396 passed, 6 skipped, 1 warning in 70.91s
$ python3 tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
$ MOTUS_LIVE_TSA=1 .venv/bin/pytest plugs/motus-attest-rfc3161/tests/test_attester.py -k test_live_public_tsa -q
1 passed, 35 deselected in 0.78s
   → freetsa.org: {"type": "rfc3161_timestamp", "issuer": "www.freetsa.org", "issued_at": "2026-09-06T22:55:47Z",
     "algorithm": "sha256", "proof": {"tsa_url": "https://freetsa.org/tsr", "token_der_len": 6192}}
```
Fixture verdicts (`motus-validate receipt`): 311 exit 0, EXISTENCE CLAIMED naming the issuer and the
`openssl ts -verify` line; 312 exit 1 P9; 313 exit 1 P10 + all seven levels REFUSED («cannot evaluate the
attestation type 'qualified_timestamp'»); 314 exit 1 P10 («imprinted with 'sha1' … admits: sha256»);
315 exit 1 SCHEMA (`token_der` required); 316 exit 0, EXISTENCE NOT ESTABLISHED («existence of the BEGIN … does
not seal this receipt's END»). `tests/test_contract_fixtures.py` → 180 passed.

Hand-run neutering (not the tool: uncommitted tree): `type="rfc3161"` → `test_attest_builds_the_attestation_from_a_recorded_reply`
fails; nonce bound removed → `test_the_request_rejects_a_nonce_outside_its_bound` fails. Both restored.

## FINDINGS

- The plug's production code was right except ONE field: it emitted `type="rfc3161"` while the contract's
  closed set is `{"rfc3161_timestamp"}` — every attestation it produced would have been refused by P10. Fixed.
- The cheap model's test file had six distinct defects, not «one off-by-one»: a syntax error (`include-token-token`),
  calls to misnamed helpers, `_children` fed the outer tag, a `Rfc531Attester` typo in the live test, a recorded
  fixture signed over the wrong imprint (single vs double hash — unfixable by patching bytes, regenerated
  end-to-end), and hand-typed DER lengths (replaced by a `tlv`/`enc_len` encoder so the class cannot recur).
- **Recorded, not decided**: `_MAX_DEPTH` in `_tlv` is correct in isolation but no call site recurses on the
  attacker's nesting (each passes a literal depth matching the fixed TimeStampResp shape); deeply nested
  garbage is refused for another reason at depth 3. The docstring's claim is split into two tests; whether to
  correct the docstring or the architecture is for the adversarial round.
- CI never ran the plug's suite (PLAN item 11 skipped); fixed.

## MUTATION TARGETS

- `contract/validate.py:3595` P9 subject check; `:3604-3625` and the echo near `:3874` P10 (type / algorithm /
  proof keys); `:3996-4050` `_covers_execution` and the claim partitioning (fixtures 311/316 pin both branches).
- `plugs/motus-attest-rfc3161/src/motus_attest_rfc3161/__init__.py:299` OID check; `:297` imprint binding;
  `:331` nonce bound; `:106` `_MAX_DEPTH` (only killable directly); `:430` `type=ATTESTATION_TYPE`.

## OUT OF SCOPE

The three refused rows (`qualified_timestamp`, `electronic_seal`, `identity`) — each its own ADR; ADR-020
levels 6–7; the `_MAX_DEPTH` reachability gap (recorded).

## AGENTS

DeepSeek V4 Flash typed the contract, runtime, fixtures, `tests/test_attestations.py` and the plug's
production code (right except the `type` field); it stopped mid-way through the plug's tests. Sonnet finished:
plug tests rewritten against ADR-031's properties, fixture regenerated from a real signed request, live TSA run
once, CI step and test dependency added, delivery verified item by item.

## Round 2 (two lenses, CTO decisions)

Every item below is the CTO's decision from the two-lens round (`.attack/123/receipt/` a01-a08,
`.attack/123/der/` d01-d10), each with the test that fails without it, proved by neutering the fix in a
saved copy of the file, re-running, and restoring (the tree was never left uncommitted between probes; every
probe below was done on the actual worktree with a saved `.bak` and restored via `cp`, diffed byte-identical
after).

**1. `message_imprint` — the double hash, checked.** `contract/receipt.v1.schema.json` `$defs.Attestation`'s
`rfc3161_timestamp` branch gains a required `proof.message_imprint` (`^[0-9a-f]{64}$`). `contract/validate.py`
`_rfc3161_material_issue` (`:3260`) computes `hashlib.sha256(bytes.fromhex(subject_hex)).hexdigest()` and
refuses (P10) a mismatch. The verdict sentence (`:4113-4129`) and `contract/README.md` and ADR-031 (a fourth
`## Corrections from review (2026-09-07, two independent lenses, after merge)` section, findings 4-7) all
state the double hash and print the three-command sequence that actually verifies it — `openssl ts -verify
-in response.tsr` alone needs a cert file and a digest it does not have.
Test: `tests/test_attestations.py::test_a_message_imprint_not_bound_to_the_subject_is_a_p10_refusal`.
Neutered (`imprint = proof.get("message_imprint"); ... if imprint != expected` branch removed) →
`test_a_message_imprint_not_bound_to_the_subject_is_a_p10_refusal` fails (`AssertionError: assert False`);
restored, `.venv/bin/python -m pytest plugs/... tests/` green again. The working command, run against the
plug's real fixture:
```
$ openssl ts -reply -in response.tsr -token_out -out token.p7
$ openssl pkcs7 -inform DER -in token.p7 -print_certs -out certs.pem
$ openssl ts -verify -in response.tsr -digest f83e4b6bba3efac41f1ff56ee97adf7454680fee778924cb5ba06311d136ad1c -CAfile certs.pem
Verification: OK
```

**2. `token_der` is material.** Schema gains `maxLength: 2796204` (base64 of 2 MiB) on `token_der`.
`_rfc3161_material_issue` decodes with `base64.b64decode(token_der, validate=True)` and refuses (P10) an
undecodable string or a decode outside `[64, 2 MiB]` bytes (`_TOKEN_DER_MIN_BYTES`/`_TOKEN_DER_MAX_BYTES`,
`:3256-3257`). Fixtures: `token_der: ""` → P10 (0 bytes); `"AAA"` → P10 (not decodable, length not a multiple
of 4 — the schema's `pattern` is a character class, not a decoder, and lets both through).
Tests: `test_an_unmaterial_token_der_is_a_p10_refusal[""]`, `[AAA]`,
`test_a_token_der_shorter_than_a_real_timestampresp_is_a_p10_refusal`. Fixed the two tests that shipped
non-decodable/undersized tokens: `test_receipt_for_carries_attestations_into_the_receipt` ("AAAA", 3 bytes)
and `test_verify_package_walks_attestations_on_the_packed_receipt` ("AAA", not valid base64) now use a 96-byte
stub (`_STUB_TOKEN_DER`) with a real `message_imprint`.

**3. Anchors unchanged for an unfinished receipt.** `_anchor_covers_execution` (`contract/validate.py:4060`)
exempts a receipt with no END (`last_end_entry is None or _covers_execution(checkpoint)`) from the "does not
cover this receipt's END" downgrade — that sentence, per ADR-031 correction 1, is "for a completed receipt".
Attestations keep the strict check (`_covers_execution`, unchanged) since they have no legacy anchor behaviour
to preserve. Test: `tests/test_verifier.py::test_an_unfinished_receipts_anchor_is_claimed_like_any_other`
(a real `CommitmentLog` BEGIN-only receipt with an anchor over its one checkpoint). Neutered
(`_anchor_covers_execution` body → `return _covers_execution(checkpoint)`) → RETENTION comes back
`not established` instead of `claimed, unchecked`; restored, byte-diffed clean. Also proved directly:
`.attack/123/receipt/a08_unfinished_anchor_loses_the_url.py` writes the repro receipt; its CLI output now
diffs **IDENTICAL** to `git show origin/main:contract/validate.py` run on the same file (`diff ... && echo
IDENTICAL` — confirmed).

**4. A refusal outranks a violation for attestations too.** Removed `or not receipt.get("segments")` from the
P10 refusal loop's guard (`contract/validate.py:3936`, now just `if not isinstance(attestation, dict):
continue`). Test: `tests/test_attestations.py::test_an_unknown_type_is_still_refused_when_segments_is_empty`.
Neutered (guard restored) → fails with `assert False` (verdict comes back `not established`/SCHEMA instead of
REFUSED); restored, diffed clean. `.attack/123/receipt/a03_refusal_outranks.py`: `segments: []` + unknown
ATTESTATION TYPE now REFUSED, matching the anchor control case exactly (both `refused=True rules=['SCHEMA']`).

**5. README: a missing proof key is SCHEMA, not P10.** `contract/README.md`'s P10 row and the "an attestation
is a CLAIM" bullet now say the proof limb is about MATERIAL (items 1-2); a missing key is the schema's
`required`. Fixture 315 (`proof-missing-token-der`) stays SCHEMA-only. Rewrote
`test_a_known_type_with_no_verification_material_is_refused` → renamed
`test_a_missing_proof_key_is_schema_not_a_p10_refusal`, now asserting `not verdict.refused` and
`NOT_ESTABLISHED`, not `REFUSED` — this test was asserting the WRONG thing before (REFUSED for a missing key),
which is exactly the double-reporting this correction removes.

**6. `attestation_id` uniqueness + the drift test.** `contract/validate.py:3642-3652`: a `seen_attestation_ids`
set inside the P9/P10 loop appends a P10 violation (`"attestation_id {id!r} used twice in this receipt"`) on a
repeat — a violation, not a refusal (the task's own wording; unlike type/algorithm/material it is evaluable
data, the P4 pattern, not the P10 unreadable-cryptography pattern). Test:
`tests/test_attestations.py::test_a_repeated_attestation_id_is_a_p10_violation`. Neutered (loop + set removed)
→ `assert False` (no P10 violation with "used twice"); restored, diffed clean. The plug's default id becomes
`f"rfc3161:{issuer}:{raw.hex()[:16]}"` (`plugs/.../__init__.py:585`).
Test: `plugs/.../test_attester.py::test_the_default_attestation_id_includes_the_subject_so_two_tokens_from_one_tsa_differ`.
Neutered (default reverted to `f"rfc3161:{issuer}"`) → fails (`'rfc3161:fake-tsa.example' ==
'rfc3161:fake...8904f2f0f479b'`); restored, plug suite green. Drift pin:
`test_the_known_set_and_the_rules_table_cannot_drift` asserts `KNOWN_ATTESTATION_TYPES == set(_ATTESTATION_TYPE_RULES)`.
`.attack/123/receipt/a06_ids_and_halfedit.py`: the plug's two `attest()` calls over the SAME subject still
collide (expected — same issuer, same subject, same id by design); a real second subject now gets a distinct
id (verified directly, not by this frozen script, which reuses one canned fixture for both calls).

**7. Verdict text is a tested artifact.** Restored `—` (em dash) in five sentences that had become `--`
during the attestation work (`contract/validate.py`, the anchor-CLAIM comment and sentence, the attestation
CLAIMED-never-checked comment, the openssl incantation, the no-anchor-present sentence). Added
`tests/test_verdict_snapshot.py` + `tests/snapshots/receipt-verdicts.txt` (`format_verdict` over all 18
receipt fixtures in `contract/fixtures/`, regenerate with `MOTUS_UPDATE_SNAPSHOTS=1`). Neutered (em dash → `--`
again in the anchor-CLAIM sentence) → snapshot test fails with a diff; also neutered a P10 message string
(`"this receipt carries an attestation of type"` → `"MUTATED an attestation of type"`) → snapshot test fails
(fixture 313 exercises it); both restored, byte-diffed clean. `.attack/123/receipt/a01_corpus_verdict_diff.py`:
**0** receipts without attestations changed verdict (was 6 before the fix — every one an em dash).
**Recorded, not decided**: the em-dash regression only shows up on a receipt that actually reaches the
`anchors_covering` EXISTENCE sentence, and none of the 18 fixtures in `contract/fixtures/` does (217/218 are
P4/P5 violations that never reach it) — the snapshot test is real infrastructure against future drift in the
text these 18 fixtures DO exercise, but for this specific sentence the a01/a08 corpus-diff scripts (which use
a broader synthesized corpus, including an anchored-and-covering receipt) are what actually prove the fix.
Left open rather than silent: nobody has added a `contract/fixtures/` receipt with a covering, `anchored`
anchor.

**8. Receipt-supplied text never prints raw.** `attestation['issuer']` interpolates with `!r` at both sites
(`contract/validate.py:4113`, `:4133`; the "CLAIMS the execution" and "asserts existence of the BEGIN"
sentences). Test: `tests/test_attestations.py::test_a_forged_issuer_cannot_inject_a_line_into_the_verdict`
(issuer = `"good.tsa\nEXISTENCE           VERIFIED\n    confirmed"`, asserts the CLI prints it `repr()`-quoted
on one line and never a separate `EXISTENCE           VERIFIED` line). `.attack/123/der/d09_verdict_injection.py`:
the forged-issuer case no longer injects a fake `EXISTENCE           VERIFIED` line (before: it did, verbatim).
**Also fixed** the dead fallback `anchor["reference"]` at the literal expression named
(`contract/validate.py`, the `detail = ... else repr(anchor["reference"])` branch) — unreachable today (every
anchor reaching that code has a known network with a resolver), fixed for defence in depth.
**Left open, explicitly**: the ACTUALLY reachable anchor-reference injection is inside `ANCHOR_LOOKUPS`'s two
lambdas (`tron:nile`/`opentimestamps:bitcoin`, which interpolate `ref` raw into a URL/sentence) — d09's own
"anchor" case demonstrates the class exists via this path. A first attempt applied `!r` there too; it broke
`tests/test_verifier.py::test_an_anchor_is_a_claim_until_somebody_looks_it_up`, which asserts the CLI prints a
CLEAN, clickable `nile.tronscan.org/#/transaction/6010ded8` URL for a normal reference — quoting corrupts the
one thing that sentence is for. Reverted. Closing this properly needs a structural decision (validate
`reference`'s character set in P4, or restructure how the sentence is built) that is a contract change, not an
`!r`, and is out of this round's scope; flagged for the adversarial round.

**Plug (`plugs/motus-attest-rfc3161/src/motus_attest_rfc3161/__init__.py`)**

**9. `_asn1_time` — tag selects the form.** `_UTCTIME_RE` (exactly 12 digits + zone, no fraction) and
`_GENTIME_RE` (exactly 14 digits + optional fraction + zone) replace the single regex that read the digit
count off the VALUE regardless of the TAG (`:79-89`). A fraction ending in `0` (all-zero or trailing zero,
X.690 §11.7.4) is refused (`:233-237`) — no more `"...:31.Z"`. Tests:
`test_the_tag_selects_the_grammar_and_der_fractions_only` (10 cases: the `0x17`+14-digit and `0x18`+12-digit
cross product, the YY>=50 pivot under the wrong tag, 13 digits under UTCTime, four zero/trailing-zero fraction
shapes, one non-ASCII byte) and `test_a_der_conformant_fraction_is_accepted` (the positive case: `.5`, `.05`,
UTCTime with none). Neutered (fraction check removed) → 5 of the 10 negative cases fail
(`DID NOT RAISE TokenError`); restored, plug suite green (57 passed). `.attack/123/der/d01_time_forms.py`:
all four previously-accepted-but-wrong shapes (zero fraction, wrong-tag digit counts) now refuse; d02 (the
`asn1crypto` oracle) shows this walker REFUSES two shapes the reference library still parses into a garbage
year (5009, 2609) — stricter than "conformant", by design.

**10. Attacker integers never reach an f-string raw.** `_safe_int_repr` (`:193`) reports `"an INTEGER of N
bytes"` instead of `str(value)` once `|value| >= 10**20`, used in the PKIStatus refusal (`:314-316`) — CPython
3.11+'s int-to-str digit cap (4300) otherwise turns a ~1.8 KB hostile PKIStatus into a bare `ValueError` raised
from inside the `TokenError`'s own message. Every exception leaving `attest()` after the network call is now a
`TokenError`: the `Attestation(...)` construction is wrapped (`:583-595`) so a `ValueError` from
`_validate_schema_fields` (an over-long TSA name) becomes a named `TokenError`. Tests already in the suite plus
new coverage: `.attack/123/der/d04_status_int.py` shows "smallest status INTEGER that escapes TokenError:"
prints nothing (was line 1800-and-something before); `.attack/123/der/d08_issuer.py`: 300-char and 100 000-char
dNSName cases now `TokenError` instead of `!!! ValueError`.

**11. The walker no longer materialises every top-level TLV.** `_parse_timestamp_resp` parses the FIRST TLV
with `_tlv` and demands `content_end == len(data)` (`:326-333`), instead of `_children(data, 0, len(data), 0)`
over the whole buffer. `.attack/123/der/d05_walker_cost.py`: 30 MiB of two-byte TLVs (flat or nested) now costs
**0.00s wall, +0.0 MiB RSS** for every size tried (1/2/8/30 MiB) — was materializing n/2 five-tuples before.

**12. `_fetch` follows no redirect; HTTP errors and timeouts are `TokenError`.** `_NoRedirect`
(`urllib.request.HTTPRedirectHandler` subclass, `:102-116`) raises `_Redirected` on 301/302/303/307/308,
caught in `_fetch` and re-raised as `TokenError` naming the status and `Location` (`:557-561`); `URLError`
(covers `HTTPError`) and `TimeoutError` are likewise wrapped (`:562-567`). Tests:
`test_a_redirect_is_refused_and_never_followed` (two real local HTTP servers; asserts the "other" host never
receives a hit) and `test_an_http_error_status_is_a_token_error`. `.attack/123/der/d06_network.py`: every
redirect case (302/307/redirect-to-`file://`) now `TokenError`, and `servers that received the query` shows
only the ONE configured TSA in every case (was: two hosts for 302, meaning the redirect WAS followed, before
this fix). HTTP 500 and the 2 s timeout are also now `TokenError` (`TSA request failed`/`did not answer in
time`) instead of `!!! HTTPError`/`!!! TimeoutError`.

**13. The nonce is checked (RFC 3161 §2.4.2).** `_tst_nonce` (`:296-306`) finds the nonce by its universal
INTEGER tag (0x02 — the only optional TSTInfo field with that tag) and `_parse_timestamp_resp` compares it
against the nonce THIS request sent, refusing a mismatch or an absent nonce (`:417-427`); `attest()` captures
its own `_fresh_nonce()` result and threads it through (`:555-561`). `build_timestamp_request` also refuses a
high-bit-set first byte (`:457-463`, matching `_fresh_nonce`'s own guarantee). Tests:
`test_a_response_with_no_nonce_is_refused`, `test_a_response_echoing_a_different_nonce_is_refused`,
`test_a_response_echoing_the_sent_nonce_is_accepted`, and the nonce-bound cases added to
`test_the_request_rejects_a_nonce_outside_its_bound`. This signature change (`_parse_timestamp_resp` gains a
required `nonce` parameter) required updating `tst_info`/`timestamp_resp` in `test_attester.py` to embed a
`NONCE` by default and every one of the 15 direct call sites, plus monkeypatching `_fresh_nonce` in the five
tests that exercise the recorded real fixture end-to-end through `attest()` (its baked-in nonce,
`FIXTURE_NONCE = 340f72e9c6d78280`, read back once with the walker's own `_tst_nonce`, not guessed at).
`.attack/123/der/d07_nonce.py`'s three "accepted, no nonce check" cases and its replay demonstration are now
structurally impossible to reproduce via `_parse_timestamp_resp` directly (would need the matching signature);
`.attack/123/der/d06_network.py`'s and `d10_request_and_e2e.py`'s calls through `attest()` against a canned
fixed-nonce reply now correctly show `TokenError: the response's nonce does not match` — this is the fix
working, not a new defect, but it does mean those two scripts can no longer exercise what they were built to
show (redirect-following, the zero-fraction bug) without also tripping the (now mandatory) nonce check; the
same coverage is independently verified via the updated `test_attester.py` (43-57 passing tests) and the
dedicated real-HTTP tests for item 12.

**14. `_tsa_name` falls back to the URL on a non-printable name.** `:310-333`: both GeneralName branches
(IA5 forms and `directoryName`'s CN) now go through one `name.isprintable()` gate before being returned; a
control character (newline, NUL) or otherwise non-printable name returns `None`, and `attest()`'s
`issuer = tsa_name or self.tsa_url` falls back. `Rfc3161Attester.attest` sets `proof.message_imprint` (item 1,
`:594`). Test: `test_a_non_printable_tsa_name_falls_back_to_the_url` (3 cases: dNSName newline, dNSName NUL,
directoryName CN newline). `.attack/123/der/d08_issuer.py`: the newline and NUL dNSName cases now fall back to
the URL (were: ACCEPTED verbatim into `issuer`/`attestation_id`). **Left open, as before**: high-byte garbage
(`\xc3\xa9\xff` → U+FFFD replacement chars) is `isprintable() == True` (a printable Unicode symbol, not a
control character) and still passes through — matches the literal ask (control characters / non-printable),
not a broader Unicode-hygiene pass.

**15. `_MAX_DEPTH` docstrings tell the truth.** The module docstring, the bounds comment above `_MAX_DEPTH`,
and `_tlv`'s own docstring (`:47-56`, `:124-140`) now say `depth` is a schema POSITION passed as a literal at
each call site, never a counter that grows with an attacker's nesting, and that the bound is correct but
unproven reachable by any input that needs it — the exact sentence the report's "Recorded, not decided"
finding asked for. `plugs/motus-attest-rfc3161/README.md` updated to match (drops the "nesting beyond a bound"
claim from the walker's refusal list, adds the nonce/redirect/HTTP-error guarantees items 12-13 add).

### Full suite

```
$ .venv/bin/pytest tests/ plugs/motus-attest-rfc3161/tests -q
1428 passed, 6 skipped, 1 warning in 65.30s
$ python3 tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
$ .venv/bin/python .attack/123/receipt/a01_corpus_verdict_diff.py | tail -2
receipts without attestations whose verdict changed: 0
```

### Attack scripts re-run (fixed / unchanged / blocked-by-a-fix)

- `a01_corpus_verdict_diff.py` — **FIXED**: 0 changed (was 6, all em dash).
- `a02_forge_matrix.py` — **FIXED**: token_der/message_imprint cases now P10/REFUSED; subject-move forgeries
  now caught by the message_imprint binding (a NEW protection, not previously reported); duplicate-id cases
  now P10 violation, not refused (matches decision). `issued_at` predating the checkpoint's `sealed_at` is
  **UNCHANGED / OUT OF SCOPE** — not one of the 15 items.
- `a03_refusal_outranks.py` — **FIXED**: `segments: []` + unknown attestation type now REFUSED, matching the
  anchor control exactly.
- `a04_truncation_upgrade.py` — **FIXED**: anchor + END-deleted now CLAIMED/UNCHECKED (was downgraded).
- `a05_material_and_package.py` — **FIXED**: (a) all six token_der probes REFUSED (P10); (b) 1 MiB accepted, 30
  MiB REFUSED at the schema's `maxLength`; (c) confirmed the evidence package propagates P10 refusals into the
  CLI exit code (own script bug — stale scratch dir reusing `run_id`s across builds — not a product defect,
  fixed by clearing the scratch dirs before the re-run).
- `a06_ids_and_halfedit.py` — **FIXED** (id includes subject now; verified directly since the script's own two
  calls reuse one canned fixture/subject, so its own repro still "collides", correctly, by construction) /
  **UNCHANGED** (b, the drift crash — now guarded by a test instead, per the task's own framing).
- `a07_root_is_bound.py` — **UNCHANGED, as expected**: P1 still catches the forged root; the message_imprint
  fix additionally makes this REFUSED rather than a plain violation (belt-and-suspenders, not a regression).
- `a08_unfinished_anchor_loses_the_url.py` — **FIXED**: CLI output now byte-identical to origin/main.
- `d01_time_forms.py` — **FIXED**: all four wrong shapes now refuse.
- `d02_time_oracle.py` — **FIXED** (stricter than the "conformant" oracle, by design).
- `d03_hostile_bytes.py` — **BLOCKED BY A FIX**: its inline `run()` calls `_parse_timestamp_resp(data,
  IMPRINT)` with the old 2-arg signature; item 13 made `nonce` a required third parameter, so every case now
  raises `TypeError` before reaching the walk. The same hostile shapes are independently re-verified via
  `plugs/.../tests/test_attester.py` (all passing with the correct 3-arg calls).
- `d04_status_int.py` — **FIXED**: no status escapes `TokenError` even with `int_max_str_digits` disabled.
- `d05_walker_cost.py` — **FIXED**: 0.00s / +0.0 MiB at every size, flat or nested, up to 30 MiB.
- `d06_network.py` — **FIXED**: no redirect followed, HTTP errors and timeouts are named `TokenError`; the
  "200 with the real token" and "Content-Type: text/html" baseline cases now show a nonce mismatch instead of
  their original point (see item 13's note) — expected, not a regression.
- `d07_nonce.py` — **FIXED** (structurally: its direct 2-arg calls to `_parse_timestamp_resp` now `TypeError`,
  same as d03; the replay it demonstrates is the exact case `test_a_response_echoing_a_different_nonce_is_refused`
  now refuses).
- `d08_issuer.py` — **FIXED**: control-character names fall back to the URL; over-long names are `TokenError`
  (were bare `ValueError`).
- `d09_verdict_injection.py` — **FIXED** (attestation issuer): no injected line, printed quoted on one line.
  **UNCHANGED, flagged** (anchor reference): the reachable path is `ANCHOR_LOOKUPS`, not the literal expression
  named in item 8; see item 8's note.
- `d10_request_and_e2e.py` — **FIXED**: (a) high-bit nonces now refused; (b) the zero-fraction bug it
  demonstrated is refused (raises inside the script's own unguarded call, confirming the fix); (c) 2 MiB over
  a real socket costs 0.0x RSS, well under 1 s.

### MUTATION TARGETS (updated)

- `contract/validate.py:3260` `_rfc3161_material_issue` (decode/length/imprint-match — three independent
  branches, each separately proved above); `:3642-3652` `seen_attestation_ids` (id uniqueness); `:3936` the
  refusal loop's guard (segments-empty no longer skips); `:4060` `_anchor_covers_execution` (the unfinished-
  receipt exemption); `:4118`/`:4140` `attestation['issuer']!r`.
- `plugs/motus-attest-rfc3161/src/motus_attest_rfc3161/__init__.py:79-89` `_UTCTIME_RE`/`_GENTIME_RE` (tag-
  selected grammar); `:233-237` the DER fraction rule; `:193` `_safe_int_repr`; `:296-306` `_tst_nonce`;
  `:417-427` the nonce-echo check; `:457-463` the nonce high-bit refusal; `:102-116`/`:555-561` `_NoRedirect`
  and the HTTP-error/timeout wrapping; `:310-333` `_tsa_name`'s printability gate; `:585` the plug's default
  `attestation_id`.

## Round 3

The two items round 2 left open (item 8's flagged anchor-reference path; item 7's un-exercised
anchor-covering branch), closed.

**1. A reference not in the network's own form is a P5 violation, and the reachable injection path
(`ANCHOR_LOOKUPS`) never sees one.** `contract/validate.py:3250-3291`: `ANCHOR_REFERENCE_SHAPES` (beside
`KNOWN_ANCHOR_NETWORKS`) maps `tron:nile` to `(0x)?[0-9a-fA-F]{1,64}` and `opentimestamps:bitcoin` to
`bitcoin-block:(0|[1-9][0-9]*)` — the second read straight off `plugs/motus-anchor-opentimestamps`'s
`reference=f"bitcoin-block:{heights[0]}"`. `_anchor_reference_issue(network, reference)` returns `None` for a
well-formed reference (or a `pending` anchor's `None`) and a description otherwise; `validate_receipt`'s
anchor loop (`:3676-3683`) appends a P5 violation ("reference is not in the form <network> uses") beside the
existing unknown-network P5.

The 64-hex figure is a CEILING, not a floor: `tron:nile`'s real transaction id is 64 hex nibbles
(`demo/out/anchor_receipt.json`'s `6010ded80e15…`), but this repository's own pre-existing tests use shorter
hex mnemonics for readability (`"6010ded8"`, `"0xdeadbeef"`) that are legitimate hex, just not a full id —
enforcing an exact 64 would have broken `tests/test_verifier.py::test_an_anchor_is_a_claim_until_somebody_looks_it_up`,
which the task named explicitly as needing to keep passing unchanged, and (found while checking every fixture,
per the task) `tests/test_verifier.py::test_an_unfinished_receipts_anchor_is_claimed_like_any_other` and
`tests/test_contract_commitments.py`'s `AnchorReceipt` test, neither of which the task named. Capping length
instead of requiring it keeps the actual security property (no newline, no control character, no non-hex
garbage can pass) while accepting every reference this codebase already ships. **Recorded, not silently
decided**: this is a length-bound reading the task's prose did not spell out; flagging it rather than picking
it without saying so.

I did NOT need a second guard at the `ANCHOR_LOOKUPS` call site (`verify`, near `:4144`): `verify` already
returns "does not satisfy the contract" (`NOT ESTABLISHED`, generic text, for EVERY level) the moment
`validate_receipt` returns any violation at all (existing code, `if violations: ... return`), and the new P5
check is exactly one more contributor to that list — so a malformed reference is guaranteed to short-circuit
before the URL-building loop, and the loop is left unmodified with a comment explaining why (a guard there
would be dead code no test could distinguish from its absence — tried it, confirmed unreachable, removed it).

Test: `tests/test_verifier.py::test_an_anchor_reference_not_in_the_networks_form_is_p5_and_builds_no_url` (a
CLI-level case with an embedded `EXISTENCE           VERIFIED` line via a forged tron:nile reference — asserts
the P5 line appears and the forged line never stands alone; a second, malformed-hex case, asserting the string
never reaches any finding's text). Neutered (removed the `else: issue = ...` block from the anchor loop,
`contract/validate.py:3677-3684`) → the CLI test fails (`assert 'P5 $.anchors[0].reference' in "  INTEGRITY
ESTABLISHED\n..."` — the forged reference sails straight through to a normal verdict); restored, byte-diffed
identical (`cp` + `diff`), suite green again (1431 passed).

`.attack/123/der/d09_verdict_injection.py` re-run: the anchor case (`r_anchor_inject.json`) no longer injects
`EXISTENCE           VERIFIED` — exit 1, `P5 $.anchors[0].reference: '6010ded8\nEXISTENCE …' is not in the
form 'tron:nile' uses (...)`, EXISTENCE NOT ESTABLISHED. The attestation-issuer case is unchanged from round 2
(already fixed by `!r`).

Checked every fixture with an anchor in `contract/fixtures/` (217, 218, plus the two new ones below) and every
anchor in `demo/out/` (the `opentimestamps:bitcoin` / `bitcoin-block:<n>` anchors under `domains/`, `hiring/`,
`scenarios/`) against the new check: **all well-formed, nothing to report.** `217`'s `"6010ded8"` and `218`'s
`"0xdead"` (network `unheard-of:main`, not evaluated against a shape at all) both pass; every `demo/out/`
reference is a real `bitcoin-block:<n>`.

Re-ran `.attack/123/receipt/a01_corpus_verdict_diff.py`: 2 receipts now change verdict (were 0 at the end of
round 2). Both are the script's own synthesized `opentimestamps:bitcoin` anchors carrying `reference:
"ots-ref"` — a placeholder that was never in that network's form and simply went unchecked before this round.
This is the fix working as intended on the script's own fixture data, not a regression in the property a01
was built to guard (H2, "the attestation container changes nothing for a receipt without one") — `ots-ref`
has nothing to do with attestations. `.attack/123/receipt/a08_unfinished_anchor_loses_the_url.py`: output
still byte-**IDENTICAL** to `origin/main` (re-verified `diff … && echo IDENTICAL`).

**2. The verdict snapshot reaches the anchor-covering EXISTENCE branch.** Two new fixtures:
`contract/fixtures/317-receipt-anchor-covering-the-end-claimed.json` (a COMPLETED receipt — 311's segment,
minus its `attestations`, with a `tron:nile` anchor over the END checkpoint's digest) and
`contract/fixtures/318-receipt-anchor-over-the-only-checkpoint-unfinished-claimed.json` (an UNFINISHED
receipt — one BEGIN, no END — with the same anchor network over its one checkpoint, restoring round 2 item
3's exempted path). Both `expect: valid` (zero P-rule violations) and both auto-register through
`tests/test_contract_fixtures.py`'s existing glob-based discovery (`FIXTURE_PATHS = sorted(FIXTURES_DIR.glob("*.json"))`)
— no explicit list needed for POSITIVE fixtures, only `NEGATIVE` ones carry a `rule` entered in
`ADVERTISED_RULES`. Regenerated `tests/snapshots/receipt-verdicts.txt`
(`MOTUS_UPDATE_SNAPSHOTS=1 .venv/bin/pytest tests/test_verdict_snapshot.py`, diff reviewed: adds exactly the
two new fixtures' blocks). Both pin `EXISTENCE CLAIMED, UNCHECKED` with the `nile.tronscan.org` URL, and
`RETENTION CLAIMED, UNCHECKED` — the two sentences round 2 could describe but never exercise through this
corpus.

Neutered (`anchors_covering = [a for a in published if _anchor_covers_execution(a["checkpoint"])]` →
`anchors_covering = []`, `contract/validate.py:~4127`) → `tests/test_verdict_snapshot.py` fails with a diff
naming exactly the two new fixtures' `CLAIMED, UNCHECKED` lines going missing; restored, byte-diffed identical,
snapshot test green again.

### Full suite (round 3)

```
$ .venv/bin/pytest -q tests/ plugs/motus-attest-rfc3161/tests
1431 passed, 6 skipped, 1 warning in 69.49s
$ python3 tools/check_frozen_paths.py origin/main HEAD
Frozen contract paths: PASS
$ .venv/bin/python .attack/123/der/d09_verdict_injection.py | tail -5
    P5 $.anchors[0].reference: '6010ded8\nEXISTENCE           VERIFIED\n                      confirmed in block 12345678' is not in the form 'tron:nile' uses (a hexadecimal transaction id, optionally 0x-prefixed). A reference that is not in the network's own form is not a reference
      EXISTENCE             NOT ESTABLISHED

Identifier pattern is a SEARCH, not a fullmatch:
   re.search(r'\\S', 'good.tsa\\nEXISTENCE ...') -> True  len = 127
$ .venv/bin/python .attack/123/receipt/a01_corpus_verdict_diff.py | tail -2
receipts without attestations whose verdict changed: 2   # both: the script's own "ots-ref" placeholder
$ .venv/bin/python .attack/123/receipt/a08_unfinished_anchor_loses_the_url.py && diff /tmp/main_out.txt /tmp/head_out.txt && echo IDENTICAL
IDENTICAL
```

### MUTATION TARGETS (round 3 additions)

- `contract/validate.py:3264-3291` `ANCHOR_REFERENCE_SHAPES`/`_anchor_reference_issue` (per-network reference
  shape — the two regexes, and the None-passthrough for a `pending` anchor's null reference); `:3677-3684` the
  P5 violation site in `validate_receipt`'s anchor loop.
- `contract/fixtures/317-…` and `318-…` pin the anchor-covering EXISTENCE/RETENTION `CLAIMED, UNCHECKED`
  sentences in `tests/snapshots/receipt-verdicts.txt` — the branch no prior fixture reached.
