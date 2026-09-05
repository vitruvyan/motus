# ADR-027 — an execution is named by its root and located by its BEGIN

- **Status:** ACCEPTED
- **Date:** 2026-09-05
- **Accepted:** 2026-09-05 by the founder
- **Authority:** CTO proposes; the founder accepts. Closes the decision #117 asks for and unblocks #118 (receipt producer) and #120 (evidence package).
- **Depends on:** ADR-019 (the derived root), ADR-021 (commitments: `(tenant, writer_id, sequence)`), ADR-023 (the resumed run, `Continuation.bundle_fingerprint`).
- **Amends:** `contract/receipt.v1.schema.json` — adds one object, `execution`, at the top level (decision 4). Nothing else in the contract changes; ADR-023 is not amended (decision 1 says why).

## Context

A run has two identities in this repository, chosen by two ADRs for two different jobs:

- **`Trace.root`** — `sha256:…`, derived by walking the chain (ADR-019, `contract/validate.py:3492`). The END commitment carries it and `commitments.py` refuses an END without one. It is `None` while the trace has no terminal record.
- **`TraceBundle.fingerprint`** — `bundle:sha256:…` over `{bundle_version, graph_spec, trace}` (`replay.py:288`). ADR-023 chose it as the name of the predecessor in a resumed run: `Continuation.bundle_fingerprint`.

And a third string that looks like a name and is not: **`run_id`**. `commitments.py` documents why — a retried job keeps its id, so a chain can hold several unpaired BEGINs under one, and a BEGIN carries no digest of the run it opens.

What consumers did with this, measured on 2026-09-04 in Limen's first end-to-end run (27 decision runs, 20 effect runs): the gateway needed a name for a decision *before the provider was called*, took `trace.root or trace.run["run_id"]`, and the fallback fires exactly for the runs a registry most needs — the ones that began and have not finished. The effect run stored `caused_by = <root of the decision>`, which is right, and the 451/403 responses carried `run_ref = <root>`, which is right too; but a deny that happened after a crash mid-run would have carried a `run_id`, and two retries would have carried the same one.

There is one more fact, and it decides the second half of this ADR: **a BEGIN commitment has a position** — `(tenant, writer_id, sequence)` — that exists the instant the run starts, is unique within its chain by construction (`CommitmentLog` refuses a duplicate sequence; #129 made a failure after the durable write poison the log rather than reissue one), and is what an inclusion proof and a checkpoint actually refer to. Nothing else about an unfinished run is both unique and provable.

## Decision

1. **The canonical execution fingerprint is `Trace.root`.** It is the only candidate the commitment chain and the anchor prove (the END carries it; the checkpoint commits to the END), and the only one derivable from the trace alone by a verifier who holds nothing else. `TraceBundle.fingerprint` stays what ADR-023 made it: the identity of a **bundle**, an artefact, which a resume must name because a resume needs the whole bundle. A receipt, a deep link, a search result and a package name an **execution**, and they name it by its root. ADR-023 is untouched.

2. **The locating key of an execution is its BEGIN's position: `execution_ref = <tenant>/<writer_id>/<sequence>`**, the three fields the BEGIN commitment already carries, joined with `/` (none of the three may contain `/`; `CommitmentLog` refuses a tenant or writer id that does). It exists before any node runs, it exists for a run that never finished, and it is unique within a chain. It is a *location*, not a proof: two logs could hold the same string, which is why it is never used across writers without the checkpoint that binds it.

3. **A run that has no fingerprint is named by nothing else.** While `Trace.root` is `None`, the receipt's `fingerprint` is `null`; it is never substituted by `run_id`, by a digest of a partial trace, or by the bundle fingerprint. A run without a commitment log and without a terminal record has **no name** and cannot be referenced; that is a consequence of running without commitments, not a gap to paper over.

4. **`receipt.v1` gains one top-level object**, optional until 1.0.0 and required from it:
   ```json
   "execution": {
     "ref":         "<tenant>/<writer_id>/<sequence>",
     "fingerprint": "sha256:…" | null,
     "run_id":      "<as the embedder supplied it>"
   }
   ```
   `ref` is the key a reader uses to ask for this receipt again; `fingerprint` is what the reader verifies; `run_id` is carried for the embedder's own correlation and is documented in the schema as *not a key*.

5. **Any API that takes an execution takes `execution_ref`.** `CommitmentLog.receipt_for(execution_ref)`, the evidence package's manifest, Perpetuum's registry. `run_id` appears in a signature only as a filter beside `sequence`, never alone.

## Consequences

- #118 and #120 can be written without inventing a name: `receipt_for("<tenant>/<writer>/<seq>")` finds the BEGIN by position, its END if any, the Merkle path and the checkpoint.
- Limen's `run_ref` changes: it becomes the `execution_ref` when a commitment log is on, and the root only as `fingerprint`; the `trace.root or run_id` fallback is removed. Its effect run keeps `caused_by = <root>` and gains `caused_by_ref`.
- **What is given up:** two names to explain instead of one. A reader who asks "which is *the* identity" gets "the fingerprint is the identity; the ref is where to find it", and the receipt carries both so nobody has to remember.
- **What is given up, and is a real loss:** executions that ran with `commitments=None` and did not finish are unreferenceable. Motus does not fabricate a name for them.

## Alternatives rejected

- **`run_id`.** Repeats on retry by design; two unpaired BEGINs under one id already exist in real chains. An API keyed on it would be ambiguous on exactly the runs that matter and would become permanent public surface.
- **`TraceBundle.fingerprint` as the execution identity.** It covers `graph_spec` and `bundle_version` too, so two bundles of one execution can differ while the execution did not; and it is not what the chain proves. Right for a resume, wrong for a receipt.
- **A new hash over the BEGIN.** A fourth identity, which #117 forbids: "two identities already exist and one of them is right".
- **An identifier minted by the log** (a UUID at `begin()`). Motus mints nothing (ADR-021 §6); a minted id would be a fourth identity with no proof behind it, and the BEGIN's position already is an identifier that costs nothing and proves something.

## Hypothesis, and how it fails

**H1 — `sequence` never restarts within a `(tenant, writer_id)` chain**, so `execution_ref` is unique for the life of the chain. This rests on `CommitmentLog._replay` counting survivors on reopen and on #129's poisoning of the live instance after a durable write. It fails if a retention policy ever truncates a chain and lets the sequence restart: #121 must therefore state that truncation drops windows from the head and never renumbers. If that turns out impossible, `execution_ref` gains the checkpoint index as a fourth segment.
