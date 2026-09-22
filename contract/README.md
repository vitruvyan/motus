# The Motus Contract

**Status: v1 — accepted by ADR-001 on 2026-08-03.**

This directory is the founding, binding basis of Vitruvyan Motus. The runtime
is implemented *inside* this contract, never the other way around: a change of
behavior that contradicts a file in this directory is a bug in the runtime, and
a needed change of contract is a versioned amendment here first — with its own
ADR — before any code moves.

Source documents: *Vitruvyan Motus — fondazione v1.1* (2026-08-03, founder-approved
direction), the Axis Vision 2026 independent review, and the Phase-A Terraveler
audit. Where this draft makes a choice those documents left open, the choice is
marked `OPEN:` and listed in ADR-001 for explicit approval.

## The ten surfaces

A contract is binding exactly where a gate checks it; everywhere else it is
documentation that lies. Each surface therefore names its counterparty and its
enforcement point.

| # | Surface | File | Counterparty | Enforcement point |
|---|---------|------|--------------|-------------------|
| 1 | Trace | `trace.v1.schema.json` | The outside world: databases, auditors, other languages, future versions of ourselves | The TraceSink validates at write, with JSON Schema **format assertion enabled** (draft 2020-12 treats `format` as annotation by default — conformant validators opt in). Dev profile: full validation. Prod profile: schema-checked writer at flush boundaries — under the `synchronous` durability profile every record IS a flush boundary, and that per-record cost is the intended price of that profile, not a hot-path violation |
| 2 | Node | `node-protocol.md` | Whoever writes node code | Record-and-compare: the runtime *captures* actual reads/writes; declarations, when present, are checked against captured reality. Verify-replay (0.6) makes the `pure` claim falsifiable |
| 3 | Graph | `graphspec.v1.schema.json` | The declaration/execution boundary | Static validation at construction. An invalid graph refuses to exist — it does not start-and-warn |
| 4 | Guarantees | `guarantees.md` | Operators and auditors | Executable: the conformance suite in `tests/contract/` and the CI benchmark gate. A release that violates either does not ship |
| 5 | Commitment | `commitment.v1.schema.json`, `checkpoint.v1.schema.json`, `receipt.v1.schema.json` | A third party holding a receipt, who has none of our code running and no reason to trust us | `validate.py`'s C, K and P rules, which **recompute** every digest rather than reading it back. The three schemas travel in the wheel for the same reason `trace.v1` does: a verifier somebody has to clone a repository to obtain is a verifier most of them will not run |
| 6 | System Manifest | `system-manifest.v1.schema.json` | An auditor, regulator-facing profile, or integration that needs one stable identity for the declared AI system configuration | `validate_system_manifest()` applies schema + SM1-SM3. It validates the declaration only; binding values are **not** verified merely by being present. ADR-035 requires a separate binding-verification operation before any runtime/graph value may be called matched |
| 7 | Risk & Control Registry | `risk-control-registry.v1.schema.json` | An auditor, governance system, or later regulatory profile that needs one exact revision of the operator's declared risks and controls | `validate_risk_control_registry()` applies schema + RCR1-RCR3. It validates shape, local identity and references only; it never reports that a risk exists, a control ran, a control is effective, or an obligation is satisfied |
| 8 | ControlApplication | `control-application.v1.schema.json` | An evidence consumer that needs one neutral record of a declared control evaluated or applied at one Motus execution boundary | `validate_control_application()` applies schema + CA1-CA2. It validates the event document only; registry, manifest and execution bindings require the separate public verifier, and no outcome elevates an ADR-020 assurance level |
| 9 | HumanOversightReceipt | `human-oversight-receipt.v1.schema.json` | An evidence consumer that needs one bounded record of a claimed human review, decision, override, escalation, or abstention tied to a Motus execution | `validate_human_oversight_receipt()` applies schema + HO1-HO4. It validates the event document only; it does not prove humanity, identity, authority, independence, legal competence, or compliance, and no action elevates an ADR-020 assurance level |
| 10 | Regulatory Evidence Profile | `regulatory-evidence-profile.v1.schema.json` | Any Motus consumer that needs to map opaque external requirement references to existing Motus evidence kinds | `validate_regulatory_evidence_profile()` applies schema + REP1. `assess_evidence_profile()` reports only evidence states (`missing`, `not_verified`, `mismatched`, `matched`); neither validity nor a complete evidence mapping is a compliance, conformity, certification, safety, or legal-sufficiency verdict |

### Regulatory Evidence Profile v1

ADR-038 adds a composition layer over evidence Motus already owns. A profile is
mapping data, not execution evidence and not law: it names an opaque external
requirement reference and lists the Motus evidence kinds that the profile
author expects for it. The v1 vocabulary is deliberately closed and contains
no predicates, expressions, scripts, legal prose, framework logic, or policy
DSL.

The public `assess_evidence_profile()` operation is local, deterministic and
read-only. Declaration artifacts such as System Manifest and Risk & Control
Registry match an expectation when they satisfy their existing Motus contract.
Execution-scoped ControlApplication and HumanOversightReceipt expectations
compose their existing binding verifiers and therefore preserve mismatched and
not-verified states rather than manufacturing success.

A profile may be wrong about a law or standard. Motus can validate and
fingerprint the profile and evaluate exactly the mapping it declares; it does
not certify the profile author's interpretation. Framework-specific profile
content is intentionally external to the Motus kernel so that changing an
external mapping does not change Motus evidence identity or require a runtime
release.

Profile v1 evaluates the candidate artifact set handed to the assessment call.
It does not search a store or choose the legally relevant artifact among many.
Artifact selection, cardinality, temporal predicates and semantic relevance are
outside v1; adding them requires a later ADR rather than silently growing a
policy language here.

Trace schema family v1 accepts the frozen 1.0 corpus and the additive 1.1
receipt/resume form. `x-current-version` is the single source for the version
emitted by the current package; old evidence remains valid without rewriting.

## Rules the contract imposes on itself

1. **Never in the hot path.** Validation lives at boundaries — graph
   construction, trace flush, effect ingress. The measured cost of per-event
   defensive validation in the predecessor kernel was 4.1× the pure
   serialization; that mistake is not repeated.
2. **Versioned, not eternal.** Every schema carries `schema_version`.
   A breaking change to any surface is a major version of the runtime.
   Consumers pin the contract version they built against; the trace they
   persist names the version that produced it.
3. **One source per fact.** No schema, version string, or invariant is
   duplicated. The first Motus runtime commit MUST add
   `tests/test_schema_version.py`, outside both frozen corpora, asserting that
   the trace schema version constant single-sourced in the package equals the
   const in `contract/trace.v1.schema.json`. The frozen corpora predate the
   package and cannot host it.
4. **Amendments leave a record.** A contract change is a PR touching this
   directory plus an ADR stating what changed, why, and what migrates.

### The commitment rules, and why they recompute

`validate.py` re-implements the leaf, node and checkpoint digests instead of
importing them from `vitruvyan_motus.commitments`. That is the point: the
contract is the authority (ADR-001), so a validator that called the
implementation would be checking the implementation against itself and would
agree with any drift. RFC 6962 domain separation is written out byte for byte
in both places, and a positive fixture fails the moment they disagree.

| Rule | What it refuses |
|---|---|
| `C1` | A witness acknowledgement that names a digest other than the leaf of the commitment beside it. An acknowledgement that does not name what it acknowledges is decoration, and it would carry a witnessed receipt's authority |
| `K1` | A checkpoint whose `count` and sequence range disagree. Something was added or removed, and `count` is the half a reader trusts |
| `K2` | Checkpoint 0 linking backwards, or a later checkpoint stating no predecessor. A null link at a non-zero index drops every window before it |
| `P1` | An inclusion path that lands anywhere other than the sealed `window_root` — including a valid path with junk appended, because a path element whose side is neither `left` nor `right` is refused rather than skipped |
| `P2` | A commitment proved against a window it was never in: a sequence outside the checkpoint's range, or a different `(tenant, writer_id)` |
| `P3` | A mode this distribution cannot establish. `QUALIFIED` needs ADR-020 levels 6 and 7, and neither exists here |
| `P4` | An anchor naming a checkpoint digest other than this receipt's |
| `P5` | An anchor on a network this validator cannot evaluate. ADR-020: *a `VERIFIED` on a chain the verifier cannot evaluate is the worst lie this system can tell* |
| `P6` | A chain of segments the receipt asserts by position and by nothing else: a later segment that continues nothing, one that names a predecessor other than the segment before it, or a segment with an `END` that has a successor — a trace that reached a terminal record cannot be resumed |
| `P7` | An `execution.ref` that is malformed, or names a `(tenant, writer_id, sequence)` other than the receipt's original BEGIN |
| `P8` | A completed receipt whose `execution.fingerprint` disagrees with the root derived from the paired trace. An unfinished receipt has no END and legitimately carries `null`. |
| `P9` | An attestation whose `subject` is not a checkpoint digest or the run root of this receipt. An assertion about a digest that is not here says nothing about this run |
| `P10` | An attestation outside the known type set, an algorithm its type does not admit (`rfc3161_timestamp` admits `sha256`), or a `proof` whose MATERIAL this validator will not trust — `token_der` that does not decode as base64 or decodes too short or too long, or a `message_imprint` that is not `sha256(subject)`. A violation AND a refusal — *unreadable cryptography must never sit on the same footing as checked cryptography*. A `proof` MISSING a required key is SCHEMA, not P10 — the schema already requires it, and P10's proof limb is about what a present key contains, never about its absence |


### System Manifest rules — declaration is not proof

ADR-035 adds the System Manifest as a sixth contract surface. It answers
**which system configuration the operator declares**, not whether that system
actually ran, whether a control was effective, or whether any legal,
certification or regulatory requirement is satisfied.

The document deliberately separates `bindings` from `declarations`.
Bindings carry values for which Motus defines an independent verification
recipe; declarations carry operator-supplied context. **Schema validity changes
neither category into proof.** The first contract step validates only what the
document itself can prove:

| Rule | What it refuses |
|---|---|
| `SM1` | Ambiguous identity inside one manifest: duplicate graph `(name, version)`, `component_id`, `policy_id`, or `control_id`. Two different entries under one local identity are a configuration a reader cannot address unambiguously |
| `SM2` | A control whose local `policy_ref` names no policy declared in the same manifest. External policy documents belong in `reference`; `policy_ref` is the local machine-checkable edge |
| `SM3` | A `created_at` value that has the required UTC timestamp shape but is not a real calendar instant. A regular expression is not a calendar |

Everything the JSON Schema can express — required fields, the closed component
kind vocabulary, canonical fingerprint shapes, rejection of undeclared fields,
and the absence of self-asserted fields such as `manifest_fingerprint` or
`compliant` — is reported as `SCHEMA`, not duplicated as an SM rule.

`manifest_fingerprint` is derived as:

```
sha256:<hex of SHA-256(canonical_json(complete manifest))>
```

where `canonical_json` is the same UTF-8, sorted-key, no-insignificant-
whitespace form used elsewhere in this contract. The digest is **not stored in
the document**. `system_manifest_fingerprint(document)` computes it.

**Binding verification is separate:** `validate_system_manifest()` stops at
the declaration boundary. The public `verify_system_manifest_bindings()`
operation then compares runtime and trace-schema versions with the Motus
distribution executing the verifier, recomputes `graph_fingerprint` from
supplied validated GraphSpecs, and compares `code_fingerprint` only with
matching validated Motus trace evidence. Missing evidence reports `not
verified`; it never becomes a match. This separation is what prevents
"well-formed declaration" from being reported as "verified system".

A valid manifest establishes none of ADR-020's seven trust levels. A signature
over a manifest could later establish who signed that declaration; it still
would not establish that the declared configuration was deployed or that a
declared control executed.

### Risk & Control Registry rules — governance intent is not execution evidence

ADR-036 adds a seventh contract surface for declarative governance state. A
registry revision says which risks and controls an operator has declared and
how they are related. It is not a ControlApplication, an assessment result, an
effectiveness claim, or a compliance verdict.

The core vocabulary is deliberately jurisdiction-agnostic. `category`,
`likelihood`, `impact`, `treatment`, and `control_type` are opaque
operator-defined labels. External profiles may map the same neutral registry
revision to laws or standards without changing its identity. The core schema
therefore contains no article numbers, framework control numbers,
certification status, legal risk class, or conformity result.

| Rule | What it refuses |
|---|---|
| `RCR1` | Duplicate `risk_id` or `control_id` values inside one registry revision. Risk and control identifiers occupy independent namespaces, but one identifier cannot name two declarations in the same namespace |
| `RCR2` | A control whose `risk_refs` entry names no risk declared in the same revision. The edge is local and machine-checkable; external mappings belong in `references` or later profile contracts |
| `RCR3` | A `created_at` value that has the required UTC timestamp shape but is not a real calendar instant |

An empty risk list, an empty control list, and an unaddressed risk are valid
declarations. None means "there is no risk" or "the governance state is
acceptable"; those conclusions require an authority outside this structural
contract. Likewise, a control may address many risks and a risk may be
addressed by many controls, but those edges make no effectiveness claim.

The registry is an exchange document, not an unbounded database export. V1
therefore caps each revision at 1,000 risk declarations and 1,000 control
declarations, each declaration at 100 references, each control at 100 local
risk edges, and free-text descriptions at 8,192 characters. These limits
bound validation and query work; larger governance estates must be split into
separately identified registry revisions rather than silently consuming
unbounded verifier resources.

`registry_fingerprint` is forbidden inside the document: canonical identity
is derived from the complete validated revision rather than asserted by the
revision itself. `risk_control_registry_fingerprint(document)` computes
`sha256:<hex of SHA-256(canonical_json(complete registry))>`. ControlApplication
is also a separate surface and is not embedded here.

### ControlApplication rules — one event is not control effectiveness

A ControlApplication reports one evaluation or application of one declared
control at one execution-scoped evidence boundary. It is separate from both
the Registry and the System Manifest, has its own derived identity, and binds
to the existing ADR-027 `execution_ref`; it does not invent another execution
key.

The closed `outcome` vocabulary is `applied`, `blocked`, `allowed`,
`not_applicable`, and `error`. These are observations about one control event.
None means effective, compliant, certified, or approved. A valid application
establishes none of ADR-020's assurance levels, and the schema has no field by
which it could claim otherwise.

| Rule | What it refuses |
|---|---|
| `CA1` | An `execution_ref` that is not the canonical ADR-027 `tenant/writer/sequence` coordinate. Tenant and writer follow the shared 200-character Identifier bound; the complete locator is capped at 8,192 characters. Empty or whitespace-only components and leading-zero or unbounded decimal sequences are refused as violations, never normalised or allowed to crash the validator |
| `CA2` | An `observed_at` value that has the required UTC timestamp shape but is not a real calendar instant |

`registry_fingerprint` is required because a control identifier without its
registry revision is ambiguous historical evidence. `manifest_fingerprint` is
optional at the document boundary because not every registry declares a
system binding; the public verifier reports whether the binding is matched,
mismatched, or not verifiable from the supplied documents. The required
`evidence.kind: motus_execution` and `execution_ref` make v1 execution-scoped;
organizational evidence remains outside this surface as ADR-036 requires.

`application_fingerprint` is likewise never embedded. The exact event identity
is `control_application_fingerprint(document)`, the same SHA-256 over the
canonical JSON of the complete ControlApplication document. Changing the
outcome, timestamp, binding, or any other field therefore changes its identity.

### HumanOversightReceipt rules — an event is not human authority

A HumanOversightReceipt records one claimed oversight event at one Motus
execution boundary. It binds the event to the existing ADR-027
`execution_ref`; it does not invent another execution identity. Its subject is
exactly one execution, one bounded decision point, or one derived
ControlApplication fingerprint. A valid receipt does not establish that the
actor is human, identified, authorised, independent, or legally competent.

The closed `action` vocabulary is `reviewed`, `approved`, `rejected`,
`overridden`, `escalated`, and `abstained`. These values describe one event,
not current approval state. Multiple receipts may address the same subject,
and a later receipt does not erase an earlier one. A correction may name the
derived fingerprint of the receipt it `supersedes`; consumers still retain
both events.

| Rule | What it refuses |
|---|---|
| `HO1` | An `execution_ref` that is not the canonical ADR-027 `tenant/writer/sequence` coordinate, with the same bounded identity and decimal sequence rules as CA1 |
| `HO2` | An `observed_at` or present `recorded_at` value that has the required UTC shape but is not a real calendar instant |
| `HO3` | An `overridden` event whose recorded replacement disposition is identical to its prior disposition. An override must preserve what it replaced and state a distinct replacement |
| `HO4` | A ControlApplication subject and optional binding that name different derived application fingerprints. Two locations for the same fact may not disagree |

Only `overridden` receipts carry `prior_disposition` and
`recorded_disposition`, and both are required. An override must name a decision
point or ControlApplication subject, never only the execution as a whole. Their values are opaque neutral
labels: Motus preserves the transition but does not own an operator's decision
vocabulary. `actor_ref`, optional `role`, and optional `authority_ref` are
claims supplied by the producer. They are not authentication or delegation
proofs. Optional manifest, registry, and ControlApplication fingerprints are
also claims until separately verified.

Free text and external references are bounded: rationale is at most 8,192
characters and a receipt carries at most 100 unique references, each at most
8,192 characters. The contract requires neither secrets nor unrestricted
private deliberation. Jurisdiction-specific roles, obligations, article
numbers, signatures, and current-state policies remain profile or integration
work outside this core event.

`oversight_fingerprint` is never embedded. The exact event identity is
`human_oversight_receipt_fingerprint(document)`, SHA-256 over the canonical
JSON of the complete receipt. Changing its actor, action, subject, timing,
binding, rationale, or correction edge therefore changes its identity.

**Binding verification is separate.**
`verify_human_oversight_bindings()` independently derives the fingerprints of
supplied System Manifest, Registry, and ControlApplication documents, checks a
supplied Motus receipt for a BEGIN at `execution_ref`, and checks that a bound
ControlApplication names the same execution. Every result is `matched`,
`mismatched`, or `not verified`; missing source material never becomes a
match. Even a complete set of matches proves neither the actor nor the event,
authority, review quality, timeliness, legal sufficiency, or compliance.

### What the verifier will not tell you

`motus-validate receipt <receipt> --trace <trace>` answers all seven ADR-020
levels, including the ones it could not reach. Four of its refusals are the
point of the tool rather than limitations of it:

- **an anchor is a CLAIM.** This validator contacts no network — it is offline
  and stdlib-only by design — so `state: "anchored"` in a receipt is something
  the holder typed. `EXISTENCE` and `RETENTION` are reported as *claimed,
  unchecked*, with the explorer URL, so the reader can settle it against a
  chain we do not operate. ADR-021 decision 8: for `EXISTENCE` it needs the
  chain, and not us;
- **an attestation is a CLAIM, and a claim about whatever its `subject`
  covers — never more.** The contract validator holds no TSA key and checks
  no CMS signature, so an `rfc3161_timestamp` attestation is reported with
  the issuer named and the incantation that would settle it. It DOES check
  the structural binding: `proof.message_imprint` must equal
  `sha256(subject's digest bytes)` — the double hash RFC 3161 itself
  requires, because `subject` is already a digest and the imprint the TSA
  actually signed is a hash of the thing being stamped. To settle the claim
  itself, base64-decode `proof.token_der` to `response.tsr` and run
  `openssl ts -reply -in response.tsr -token_out -out token.p7`, then
  `openssl pkcs7 -inform DER -in token.p7 -print_certs -out certs.pem`, then
  `openssl ts -verify -in response.tsr -digest <message_imprint> -CAfile
  certs.pem` — never as established here.
  ADR-031 decision 7: for a completed receipt, EXISTENCE *of the execution*
  is reported only when `subject` is the run root or a checkpoint whose
  sealed range includes the END; a token over the BEGIN's checkpoint alone
  yields "existence of the BEGIN no later than T" and EXISTENCE for the run
  stays NOT ESTABLISHED — the same sentence is said for an anchor whose
  checkpoint does not seal the END. No attestation moves `mode`;
- **a witness acknowledgement does not establish `EXECUTION_CONTINUITY`.** It
  is bound to the commitment, which is what makes its signature checkable by
  somebody holding the witness's key. This validator holds none, and says so;
- **an unknown hash algorithm, anchor network, attestation type or
  signature algorithm ends the answer.** Not a downgrade — a refusal, and the
  CLI exits non-zero, so a caller reading only the exit code cannot mistake
  *I cannot tell* for *verified*. **A refusal outranks a violation**: an
  unknown anchor network or an unknown attestation type is also a P5 or P10
  violation, and reporting it as one says "this document is wrong" about a
  document that may be perfectly correct on a chain we cannot read or under
  a type we cannot read.

**What an attestation is not** (ADR-031 decision 7). It is not a witness
acknowledgement — that stays on the BEGIN, in `commitment.v1`, under C1; it
is not an anchor — nothing is *pending*: a token is issued or it is absent;
and it is not verified by this validator — it is claimed, with the issuer
named. A receipt whose only support for EXISTENCE is an `attestations` entry
sits at level 2 *on the issuer's word*; a receipt with an anchor sits at level
2 *on a chain's record*, and the verdict text says which, because a buyer
will ask.

## Fingerprints (canonical form)

Where a schema references a fingerprint, it means: SHA-256 over the canonical
JSON encoding of the object — UTF-8, keys sorted lexicographically, no
insignificant whitespace, strict RFC 8259 (string keys only, no NaN/Infinity,
strings that are Unicode text — see below),
integers only in fingerprinted numeric positions (floats forbidden there
precisely because their canonical representation is not settled until the
integrity schema 1.1 fixes it) — prefixed with the fingerprint kind, e.g.
`graph:sha256:<hex>`. `graph_fingerprint` is computed over the canonical
encoding of the ENTIRE GraphSpec document as validated (no materialized
defaults); the validator recomputes it (SB2) — every fixture fingerprint is
TRUE, never decorative. Hashes are computed over the canonical object form,
never over the bytes of a particular encoding (JSON vs JSONL).
`code_fingerprint` is computed over the ordered node identity list —
declared name, qualified name, source hash, config fingerprint — per the
exact recipe in `node-protocol.md` §6.3.

**What that means for storage, said here because an integrator's first
question is exactly this one, and it has two halves that point opposite ways.**

**A store may reorder object keys freely.** Integrity digests are computed
over the canonical serialisation, so a trace read back with its keys in a
different order validates. The first external integrator wrote the opposite
into their own notes in three places — *never store a trace as `jsonb`,
Postgres reorders keys and the chain is over bytes* — and found out by testing
it.

**A store may not renumber.** From trace schema 3.0.0, rule `J2` (ADR-024)
requires every numeric lexeme to be what serializing its parsed value
produces. Motus writes `1e-06`; a store that hands back `0.000001` has written
the same *value* and a different *document*, and the validator refuses it —
correctly, because that is the whole point of `J2`. The two halves are not
inconsistent: **whitespace and member order are absorbed by the digest and
numeric lexemes are not**, which is exactly the line ADR-024 draws.

**At 3.2.0 and above, J2 and J4 do not co-occur — and J2 stays in scope
anyway.** From 3.2.0 every number is an integer (`J4`), so the only lexemes
`J2` could still catch there are the ones `J4` already refuses (a float, or
an integer beyond the safe range, rewritten as a different lexeme of the same
kind never reaches a document `J4` accepts). `J2` is not dropped from 3.2.0's
scope for that reason: a future version that keeps `J4` but not `J2` would
silently reopen the numeric-lexeme hole for whatever `J4` no longer covers,
and nothing would notice. `J2` stays governed so that decision has to be made
on purpose, by a version that says so, rather than by omission.

**And there is a third thing a store does, which this section did not name
until #116 found it the hard way: it may read a document with the wrong
number form.** From trace schema 3.2.0, rule `J4` (ADR-030) closes that: every
number in a 3.2.0+ trace is a JSON integer with |n| ≤ 2^53 − 1, and integers
within that range serialise identically in every JSON implementation in use —
so a 3.2.0 trace survives `JSON.parse` + `JSON.stringify` in a browser byte
for byte, which is the property the freeze promises. A store or a reader that
parses numbers and re-renders them cannot change a 3.2.0 trace's meaning, and
the validator applies `J4` to exactly those documents.

**Resuming a pre-3.2.0 segment that carries a float is refused, not silently
accepted (2026-09-06 review correction).** `J4` governs the WRITER, so a
value it refuses surfaces the moment a resumed run tries to seed
`run_started.initial_state` or the header's `metadata` from the old segment —
before any record of the new segment is stored. The consumer migrates the
value (to an integer at a declared scale) before resuming; the old segment
itself is untouched and keeps loading, replaying and verifying under its own
version, exactly as it did before this rule existed.

**For documents below 3.2.0 the canonical number form is CPython's, and that
is a requirement on a verifier written in another language, stated here so it
is a choice rather than a discovery.** A verifier in another language that
wants to re-derive the root of a 1.x–3.1.x trace MUST replicate CPython's
`repr(float)` (and its `json.dumps` number emission) exactly. The two
implementations disagree about exactly three classes of value, each measured:

- **integral floats** — `-14.0` is written `-14` by JavaScript and `-14.0` by
  Python, `0.0` and `118.0` included;
- **floats outside [1e-4, 1e16)** — the two switch to exponent notation at
  different thresholds (`1e-06` against `0.000001` at the small end);
- **integers beyond 2^53** — where a double cannot represent every integer
  (`9007199254740993` is written `…993` by Python and `…992` by JavaScript;
  the boundary witness is `2^53 + 1`, not `2^53`).

Nothing else disagreed across two measured samples of tens of thousands of
values. Nobody can re-derive those roots without replicating `repr(float)`;
that is the honest sentence, and it is why the freeze needed `J4` rather than
a better float printer.

**A Motus string is Unicode text.** Every string in a Motus document — a value
or a member name — MUST denote a sequence of Unicode scalar values. An
unpaired surrogate (U+D800–U+DFFF with no partner) denotes no character, has
no UTF-8 encoding, and conforming JSON implementations disagree about what to
do with one — some refuse it, some substitute U+FFFD, some pass it through. A
document containing one therefore has more than one reading and is refused
(rule `J1`, ADR-026).

**The two sides are scoped differently, and deliberately.** A producer refuses
always: it writes trace schema 3.0.0 and that is where the format is defined. A
READER refuses from trace schema `2.0.0`, because releases up to 0.7.0 wrote
schema 1.x traces carrying such a string and the validators shipped alongside
them called those traces valid. Refusing them now would break the promise one
paragraph up — that evidence written before a rule stays valid without being
rewritten — and this contract ranks a wrong refusal above a missed violation.

**This is not a rule about escape forms.** `"appro\u0076ed"` and `"approved"`
are the same JSON string, share a root, and both stay valid; so do
`"\ud83d\ude00"` and the character it denotes, because a surrogate *pair* is
a character. What is refused is a string that denotes nothing.

A producer carrying bytes that are not Unicode — a filename a filesystem
handed over, a payload from a legacy source — encodes them explicitly:
base64, hex, or bytes with a declared encoding. A runtime's private convention
for smuggling such bytes through a string (Python's `surrogateescape` is the
one this project met) does not travel to a reader in another language, and the
format does not adopt one language's workaround as its meaning.

So: keep the bytes if you want a stored trace to keep validating, or satisfy
yourself that your column type preserves numeric lexemes before you rely on
one that parses them. A `text` column always does.

**And note when the first half was measured.** That test ran against 0.10.0,
before `J2` existed; it was true then and is conditional now. A measured fact
carried past the change that invalidated it is the most convincing kind of
wrong, which is why the version is named here rather than left out.

## Executable fence

The schemas alone cannot enforce the R-rules and T-rules. The fence is
executable and versioned in this repository:

- `contract/validate.py` — the semantic validator: GraphSpec R1–R12; trace
  T1–T13 (record coherence, including replay monotonicity, and from 3.0.0 the
  terminal digest that makes a root anchorable, and from 3.1.0 whether
  violations may be null), E1–E11 (the execution state machine), SB1–SB4
  (spec binding, including recomputed graph fingerprints and from 3.1.0
  whether null matches the declaration),
  H1–H2, J1, J2, and from 3.2.0 J4 (ADR-030: every number in a trace is a
  JSON integer with |n| ≤ 2^53 − 1), JSONL1–3; JSON and JSONL forms. H2 binds
  resume provenance to a distinct run identity. It reads its input as BYTES
  and decodes explicitly — a reader that laundered CRLF into
  LF would judge a document the file does not contain.
- `contract/fixtures/` — versioned positive and negative instances; every
  negative declares the rule it violates, and the contract tests assert it
  fails for that reason and no other.
- `tests/test_contract_fixtures.py` — runs metaschema, schema, and semantic
  validation over all fixtures.

A contract change that does not update fixtures alongside it is incomplete
by definition.

## Gates required before implementation — status

Surface 4's full enforcement names three further gates. All three are now in
the tree and executable; implementation remains subordinate to them.

| Gate | Status |
|---|---|
| `tests/contract/` — the inherited Axis 0.4.0 conformance corpus (guarantees.md §5), ported without weakening | **present** |
| `tests/compat/terraveler/` — the frozen Terraveler corpus (guarantees.md §4), golden included | **present** |
| the CI job asserting guarantees.md §3 against the baseline in `benchmarks/` | **present** — `Jenkinsfile`, stage `slo-baseline` |

### The frozen corpora, and the one file that may move

`tests/contract/` and `tests/compat/` state what the runtime must do. The
implementing agent may not edit them: a runtime that cannot satisfy them is
wrong, and a corpus that bends to the implementation proves nothing.

They do have to name a package, and that name changes exactly once — when the
runtime moves from `axis` to `vitruvyan_motus`. That rename touches
`tests/contract/kernel.py` and nothing else; it is the single editable file in
either directory, and it resolves to the **compatibility view**, not the
Motus-native surface. If a name it exports cannot be provided, that is an
amendment with its own ADR.

The golden in `tests/compat/terraveler/golden/` is a real row lifted out of
`ingestion_runs` — six top-level keys, no `metadata`, naive timestamps inside
its events. It is evidence precisely because nobody wrote it for a test.

Mechanical protection is provided by the controller-owned Jenkins job
`Motus / frozen-contract-guard` and `tools/check_frozen_paths.py`. The job
materializes the checker from the current trusted `main` tip, uses the merge
base only as the diff boundary, and compares it with the exact pull-request
head; code in a pull request is data and cannot weaken or remove the judge. The
sole exception is `tests/contract/kernel.py`, exactly as described above.

## What lives where

- `trace.v1.schema.json` — the trace document: run header, transition
  records, routing records, lifecycle records, redacted values, reserved
  integrity fields.
- `graphspec.v1.schema.json` — topology as data: nodes, entry, transitions
  (linear, routed, terminal), effect classes, optional declared read/write
  sets.
- `commitment.v1.schema.json` — one BEGIN or END as stored in a window file
  under the on-disk envelope key `commitment`, or handed to another party,
  with the witness acknowledgement BESIDE the digested body and never inside
  it. Includes the `continues` link a resumed segment carries (ADR-023).
- `checkpoint.v1.schema.json` — one sealed window: the Merkle root over its
  commitments, the range it covers, and the link to the checkpoint before it.
- `receipt.v1.schema.json` — what a holder presents to a verifier: a RUN, as
  the ordered chain of segments it actually was. ADR-021 decision 7 — a receipt
  carrying only the checkpoint and the transaction proves a checkpoint and not
  a run. Every segment has a BEGIN; only the LAST may have an END, because a
  trace that reached a terminal record cannot be resumed. It travels alone, so
  it declares its own version, and the mode it CLAIMS must be supported by what
  it carries. ADR-027 decision 4 optionally adds `execution`: the BEGIN locator,
  the Trace.root fingerprint (or null for an unfinished execution), and a
  `run_id` carried for embedder correlation, explicitly not as a key.
- `node-protocol.md` — normative obligations of node code (RFC-2119
  language).
- `guarantees.md` — the five invariants, durability profiles, replay
  semantics stated honestly, SLO table, the Terraveler compatibility
  surface, and the inherited Axis 0.4.0 conformance corpus.
