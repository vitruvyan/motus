# PLAN-123 — ADR-031: an anchor is a chain, an attestation is a party

Sole implementer (per the brief's FLOW exclusion: no architect, no other agents; the
adversarial rounds are run by the CTO afterwards). Branch `factory/123-attestations`.
Spec: `adr/ADR-031-an-anchor-is-a-chain-and-an-attestation-is-a-party.md` (read in
full), premises ADR-020 decision 8 and ADR-021 decisions 7–8 (read in full).

## Every file I will touch, and why

| # | File | What changes | Why |
|---|---|---|---|
| 1 | `contract/receipt.v1.schema.json` | optional top-level `attestations` array; `$defs.Attestation` (seven required fields, no `signature`, `additionalProperties: false`); if/then per `type` fixing `proof` shape (`rfc3161_timestamp` → `token_der` base64 + `tsa_url` required, `additionalProperties: false`) | ADR-031 decisions 2–3; the if/then mirrors `$defs.Anchor`'s `anchored`→reference/published_at. Nothing else in the schema changes (H2: every existing receipt stays valid byte for byte) |
| 2 | `contract/validate.py` | `KNOWN_ATTESTATION_TYPES = frozenset({"rfc3161_timestamp"})` beside `KNOWN_ANCHOR_NETWORKS`; `_ATTESTATION_TYPE_RULES` (admitted algorithms, required proof keys, verdict sentences); rule P9 (subject not a checkpoint digest or run root); rule P10 (unknown type / algorithm the known type does not admit / proof lacking the type's required keys) — violation in `validate_receipt` **and** refusal at `verify()` (the P5 pattern); the levels verdict: EXISTENCE CLAIMED (the `UNCHECKED` "claimed, unchecked" status anchors use) naming the issuer and the `openssl ts -verify -in response.tsr` incantation, only for what `subject` covers (run root or checkpoint whose sealed range includes the END → execution; a BEGIN-only checkpoint → "existence of the BEGIN no later than T", EXISTENCE for the run NOT ESTABLISHED); the same coverage sentence for the anchor path (ADR-031 decision 7, review correction 1: "the validator says it for both") | ADR-031 decisions 5–6 + review corrections 1, 3; `KNOWN_*` beside `KNOWN_ANCHOR_NETWORKS` is the ADR's explicit "same ceremony" |
| 2 | `contract/README.md` | rows `P9` and `P10` in the commitment-rules table; one refusal bullet ("attestation is a CLAIM / unknown type or algorithm refused"); one paragraph "what an attestation is not" (ADR-031 decision 7) | ADR-031 decision 6 & "Amends contract/README.md" |
| 3 | `contract/fixtures/225…232` | positive rfc3161 receipt; P9 negative; P10 negative ×3 (unknown type; sha1 under rfc3161; proof missing `token_der`) | the brief's fixture list; the corpus is the executable fence |
| 4 | `tests/test_contract_fixtures.py` | `ADVERTISED_RULES` gains `"P9"`, `"P10"` | a new rule with no fixture is a rule nothing proves (the file's own preamble) |
| 5 | `tests/test_attestations.py` (new) | the four-row table (ADR-031 decision 4) enumerating known and refused types and failing when a fifth appears; CLAIMED verdict for root/END-covering subjects; the two-window BEGIN-only case; `attestations: []` ≡ absent; refusal ×3 with non-zero CLI exit; `receipt_for(attestations=...)`; evidence `pack(attestations=...)` + `verify_package` walk | the brief's items (5)(6); every verdict property with a test |
| 6 | `tests/test_commitments_schema_fields.py` | `_CLASSES` gains `Attestation`; the classification parity and bounds tests are parameterized over it | the dataclass must be held to the same contract-boundary standard as `AnchorReceipt` |
| 7 | `src/vitruvyan_motus/commitments.py` | frozen `Attestation` dataclass (`_SCHEMA_FIELDS` validation, J1-strict `proof`, `to_dict()`); `Attester(Protocol)` with `attest(subject: bytes) -> Attestation` beside `Anchor`; `__all__` | ADR-031 decision 3; "beside `Anchor`" is the brief's explicit placement |
| 8 | `src/vitruvyan_motus/commitlog.py` | `receipt_for(..., attestations=())` carrying them into the receipt | the container travels on the receipt |
| 9 | `src/vitruvyan_motus/evidence.py` | `pack(..., attestations=())` passed into `receipt_for` | verify_package already walks the receipt; the receipt now carries attestations |
| 10 | `plugs/motus-attest-rfc3161/` (new) | `pyproject.toml` (zero deps), `README.md`, `src/motus_attest_rfc3161/__init__.py` (DER TimeStampReq builder, bounded stdlib DER walker over TimeStampResp, genTime + TSA GeneralName extraction, no pyasn1/cryptography — H1), `tests/test_attest.py` against a recorded offline fixture, one opt-in live test behind `MOTUS_LIVE_TSA=1` | brief item (7); **H1 falsified (a dependency is needed) → STOP that part and report** as the brief says |
| 11 | `.github/workflows/ci.yml` | the `anchor-plugs` job installs and runs the new plug's suite | the plug is a separate distribution, CI's own lane; kernel `tests/` never sees it |

## Decisions on the ambiguous corners

- **P10's "proof lacking the type's required keys"** is enforced at the schema layer
  (if/then) and mirrored in the verify()-level refusal exactly like P5 (unknown
  network = schema-free, P5 semantic; proof keys = schema-enforced, P10-refusal echo).
  The three negative fixtures the brief names are all non-zero-exit refusals:
  unknown type and sha1 trip rule `P10`; missing `token_der` trips `SCHEMA` (the
  if/then) — all three refuse inside `verify()` (that is what "each a refusal" pins),
  and the `ADVERTISED_RULES` set gains both `P9` and `P10`.
- **CLAIMED status** = the existing `UNCHECKED` ("claimed, unchecked") constant, the
  same status the anchor path reports; ADR-031 says "same posture as an anchor, same
  reason". Never ESTABLISHED.
- **Coverage**: for a completed receipt, `subject` covers the execution iff it is the
  run root or the digest of the checkpoint whose sealed range includes the END (that
  checkpoint is unique and is the END entry's, by P2). For an unfinished receipt
  nothing can cover "the execution"; a checkpoint claim reports existence of the
  BEGIN no later than T. Anchor path gets the same coverage sentence (review
  correction 1: "the validator says it for both").
- **Empty vs absent**: `attestations: []` and absent are identical by construction —
  the verdict logic branches only when the list is non-empty; test pins byte-equal
  verdicts.
- **Never a fifth row**: the four-row table test literal-pins the closed set and
  asserts the code's known-set equals the "added" column; adding a fifth member
  fails the suite until somebody writes its row (ADR-031 decision 4).
- **H2**: the schema's only change is an optional array; every existing receipt
  and the compat corpus re-verifies byte for byte (corpus + fixtures re-run in the
  suite).

## Verification sequence

1. schema/metaschema + all existing contract fixtures green,
2. kernel suite (`tests/`) green,
3. frozen-path guard (`tools/check_frozen_paths.py origin/main HEAD`) — never touch
   `tests/contract` or `tests/compat`,
4. plug suite offline (recorded fixture),
5. each new rule's test fails without its fix (neutering spot-checks, the project
   rule), then REPORT.md under `.factory/tasks/done/012-issue-123-attestations/`.