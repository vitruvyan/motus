# Terraveler: migrate to Motus, then anchor a verdict on TRON

Paste this whole file as the opening prompt of a session working in the
`terraveler` repository.

---

## What you are doing, and why

Terraveler already runs on **Axis** — the predecessor of Motus — and does not
install it: there are two hand-copied trees, `ingest/axis/` and `rag/axis/`.
A vendored copy drifts silently, which is exactly what `vitruvyan/motus`
issue #49 describes.

Motus is the successor: same lineage, same `GraphState` shape, but it produces
a **trace** that a separate program can check, with a per-record hash chain and
a per-trace root. That root is the thing you will eventually publish on TRON.

The reason this matters for Terraveler specifically, and not as an
architecture exercise: Terraveler's whole claim is provenance. It quotes
verbatim, cites sources, and declares where evidence was destroyed rather than
guessing. A curator's verdict that can be edited afterwards, silently, is the
one hole in that claim. Closing it is what this work is for.

Do the three phases **in order**. Do not start a phase before the previous one
is verified. Report what you did at the end of each, and stop if a phase does
not verify.

---

## Phase 1 — Install Motus, delete the vendored copies

### 1.1 Install

> **Read this before installing anything.** The 0.8.1 you may already be running
> writes traces whose root is **not anchorable**: under trace schema 2.0.0 the
> terminal digest covered the terminal record alone, so an editor could rewrite
> the run and reseal it by the published recipe with that value unmoved
> (`vitruvyan/motus` ADR-019). Worse, with a fixed clock — which declared
> reproducibility gives you — three unrelated runs shared one root.
>
> Those traces are still valid, replayable evidence. They are not something to
> publish on a chain. **Phase 3 must not run until you are on trace schema
> 3.0.0**, and phase 2 should not fill a root column before then either, because
> every value it stores would have to be discarded. Phases 1 and 2 are otherwise
> unaffected: the API, the `GraphState` shape and the import changes below are
> identical.

**Motus is not on PyPI yet.** Publication by Trusted Publishing is prepared but
deliberately held (`vitruvyan/motus` PR #72, open on purpose). So install from
the repository, pinned to a commit rather than to a moving branch:

```bash
pip install "git+https://github.com/vitruvyan/motus@v0.10.0"
```

**Pin the tag, and pin this one.** `508fd52` (0.8.1) is what this document was
first written against and is deliberately not the pin any more.

- **0.9.0** is the first release that emits trace schema 3.0.0, so it is the
  earliest version that can satisfy the phase 3 condition above. That makes it
  the *minimum*, not the recommendation;
- **0.10.0** is what to take. Everything it adds — the commitment log, the
  `BEGIN`/`END` lifecycle, the anchoring-cadence arithmetic — is **off unless
  configured**, and a test in that repository runs an unconfigured graph in a
  subprocess and asserts that none of those modules is even loaded. For an
  integration that does not configure them, 0.10.0 behaves as 0.9.0 does. So
  there is no reason to take the older one and one reason not to: the newer one
  is what the next release is measured against.

**What 0.10.0 does NOT give you**, so you do not go looking: the receipt
verifier — `motus-validate receipt <receipt> --trace <trace>`, which reports
which of the trust model's levels a receipt establishes — is on `main` and is
**not in any release yet**. If your test needs to verify a receipt rather than
produce one, say so and take a pinned `main` commit for that step alone; the
rest of this document stands either way.

Terraveler's Python side has **no `requirements.txt` and no `pyproject.toml`**
today. Create one — `requirements.txt` at the repository root is enough —
pinning the Motus git URL at that commit and the other imports the Python
scripts already use (`openai`, `psycopg`, whatever `ingest/` and `rag/`
actually import). An
undeclared dependency set is the same failure as a vendored copy, one level up.

### 1.2 Change the imports

There are exactly **six import lines**, in four files:

```
ingest/pipeline.py:23     from axis.state import Fact, Decision, Rejection
ingest/extract.py:57      from axis import GraphState, Runner, Policy
ingest/extract.py:58      from axis.state import Fact, Decision, Rejection
ingest/run.py:19          from axis import GraphState, Runner, Policy
rag/app/chat_graph.py:25  from axis import GraphState, Runner, Policy
rag/app/chat_graph.py:26  from axis.state import Fact, Decision, Rejection
```

They become imports from `vitruvyan_motus.compat`, which exists for exactly
this migration and says so in its own docstring.

**The one trap.** `compat` deliberately exposes **no public `Decision`**. The
legacy type is `LegacyDecision`, because a name that means two different things
across a migration is worse than a rename. So:

```python
from vitruvyan_motus.compat import GraphState, Runner, Policy
from vitruvyan_motus.compat import Fact, Rejection, LegacyDecision as Decision
```

Aliasing at the import keeps every call site unchanged. Do not rename call
sites in this phase — one change at a time.

The full compat surface is: `GraphState`, `Runner`, `Policy`, `NodeFailed`,
`Fact`, `LegacyDecision`, `Rejection`, `FileTraceObserver`, `retry`,
`ConcurrentRunner`. If a call site needs something not in that list, **stop and
report it** — that is a finding, not an obstacle to work around.

### 1.3 Delete the vendored trees

```bash
git rm -r ingest/axis rag/axis
```

### 1.4 Verify before moving on

- `python -c "import vitruvyan_motus; print(vitruvyan_motus.__version__)"`
- run the ingestion pipeline end to end on one small source and diff the audit
  output against a run recorded before the change — it must be identical;
- `grep -rn "from axis\|import axis" --include="*.py" .` returns nothing;
- the existing tests in `ingest/` (`test_verbatim.py`, `test_language.py`,
  `test_reply_shapes.py`) still pass.

If the audit output differs, **stop**. A compat layer that changes behaviour is
a bug in Motus and it must be reported to `vitruvyan/motus` with the diff, not
absorbed here.

---

## Phase 2 — Put the curator's verdict on a native Motus trace

`compat` keeps the old surface readable; it does **not** produce the evidence
this project needs. That comes from the native API.

Migrate **one path only**: the Curator's verdict pass, `scripts/desk_review.py`.
It is the right one because Carta §2 gives the Curator the power to issue
`approved | rejected | changes-requested` and §5 makes every verdict motivated,
cited and appealable — which is a graph with decisions and routing, and it is
the thing whose integrity someone would later dispute.

### What to build

A `GraphSpec` whose shape mirrors what the script already does. Roughly:

```
load_submission → check_sources → check_verbatim → decide → record_verdict
```

with a **route** on the verdict decision, so the branch taken is a fact in the
trace rather than an `if` no reader can see.

Rules that are not negotiable:

- **The trace records shapes, not text.** Counts, lengths, scores, source
  identifiers, boolean checks. Quotation text and draft bodies stay in the
  database. Terraveler already works this way and Motus is built for it.
- **The criterion is read or mutate, and nothing else.** Nodes that READ the
  outside world — a `SELECT`, the embedding service, an HTTP GET — declare
  `effect_class: "recorded_effect"`: the result is the effect, and repeating
  it costs a round trip. Nodes that CHANGE something out there — an `INSERT`,
  a message sent, a POST — declare `"external_effect"`, and resume will refuse
  to restart them without a non-empty idempotency key and a completed receipt.
  Nodes that only compute declare `"pure"`.

  Getting this backwards in the safe direction — a read declared
  `external_effect` — only blocks resumes that were safe. Backwards the other
  way is the dangerous one and it is silent: a write declared
  `recorded_effect` never meets the resume guard at all.
- A false `"pure"` claim is **falsifiable** by verify-replay, which is not the
  same as always caught. Replay re-executes the node and compares the result,
  so a database, file or cache that answers the same way twice produces no
  divergence and the node is reported verified. It catches the claim exactly
  when the hidden dependency changes the observed result — treat it as a net,
  not a proof, and do not lean on it while classifying.
- Every verdict must be a recorded `Decision`, and the routing must be
  **on that decision**. A verdict that the graph took but did not record is the
  failure this whole exercise exists to prevent.

### Persist the trace

Write the trace next to the submission — a `verdict_traces` table, or a JSONL
column, whatever fits the Supabase schema. Two requirements:

- store the **canonical JSONL**, not a re-serialisation of your own;
- store the trace's **root** (`result.trace.root`) in its own column. Phase 3
  publishes that value and nothing else.

  `trace.root` can return `None`, and when it does the column stays empty and
  nothing is published. It means one of: the run has no terminal record, the
  trace is below schema 3.0.0, or **its chain does not verify** — the property
  recomputes the whole chain from the document rather than reading the field
  back. Treat `None` as "this evidence is not anchorable", never as "use the
  raw field instead": `records[-1]["integrity"]["payload_hash"]` returns a
  string for a rewritten document too, and it is the string an anchor would
  agree with. That is the entire reason the accessor exists.

### Verify before moving on

Motus ships its own checker, installed with the package:

```bash
# The artifact word must match how you stored it. Phase 2 above requires
# canonical JSONL, and `trace` parses the whole file as ONE JSON value -- so
# a correctly persisted trace fails this gate for the wrong reason.
motus-validate jsonl path/to/verdict.trace.jsonl

# Only if you kept a single-document copy instead:
motus-validate trace path/to/verdict.trace.json
```

Exit `0` means the trace is internally consistent. Then prove it is not
decorative: change one recorded value in a copy of the file and run it again.
It must fail, naming the rule — you should see `T11` (the hash chain) and
likely `T8` (the routing no longer matches the decision it cites). Put that
tamper check in the test suite.

---

## Phase 3 — Anchor the root on TRON

**Read this first:** Motus does **not** ship an anchor, and it does not hold
chain credentials. Decision on record (`vitruvyan/motus` issue #51): the
repository provides *the socket, not the plug*; concrete anchors live on the
consumer side. Motus's anchor interface is not built yet, so for now you read
`trace.root` yourself and publish it. Write your anchor code so it can later sit
behind that interface: one function in, one receipt out, no Motus internals.

### Getting the credentials — a human does this once, in a browser

You cannot do this part yourself: it needs an email confirmation and a faucet
that rejects automation. Ask for the five values below, then continue.

**1. TronGrid API key** — the RPC provider.

- register at `https://www.trongrid.io` and confirm the email;
- open the dashboard, `API Keys` → create one, name it `terraveler-nile`;
- the free tier is in the order of ~15 requests/second and ~100k requests/day —
  read the exact quota off the dashboard rather than trusting this line;
- it travels as the HTTP header `TRON-PRO-API-KEY`. In `tronpy`:

  ```python
  from tronpy import Tron
  from tronpy.providers import HTTPProvider

  client = Tron(HTTPProvider(endpoint_uri=TRON_ENDPOINT, api_key=TRON_API_KEY))
  ```

  One key works for mainnet and for Nile. Nile often answers without a key at
  all, at a lower rate limit — do not let that tempt you into shipping keyless
  code, because the same path will run against mainnet one day.

**2. A Nile address and its private key.** Either:

- *TronLink* (browser extension): create a wallet, write the seed phrase down
  on paper, switch the network selector to **Nile**, then export the private
  key of that account; or
- *headless*, which is enough for a testnet:

  ```python
  from tronpy.keys import PrivateKey
  pk = PrivateKey.random()
  print(pk.hex())                                  # TRON_PRIVATE_KEY
  print(pk.public_key.to_base58check_address())    # TRON_WALLET_ADDRESS
  ```

**3. Test TRX.** The Nile faucet at `https://nileex.io` gives **1000 TRX** per
address per day. Take the FIRST form on that page — *Get 1000 test coins*. The
others below it hand out TRC-20 and TRC-10 tokens (BTT, USDT, JST, TRN, WIN)
which live *on* the chain and pay for nothing: bandwidth is paid in TRX and
nothing else. Paste the address, wait for the transaction, check the balance on
`https://nile.tronscan.org`.

**4. A destination address.** Generate a second key the same way. Sending to
yourself also works — the memo is recorded either way — but a distinct
destination makes the anchor stream readable in the explorer.

A memo-carrying transfer usually exceeds the free daily bandwidth and burns a
fraction of a TRX. On 1000 test TRX that is not a constraint; on mainnet it is
the recurring cost of this design, and it is worth stating in your report.

### Configuration

All of it goes in **`terraveler/.env`**, which is already in `.gitignore`.
**Never** in the motus repository — its kernel reads no environment variables at
all, by design.

```
TRON_NETWORK=nile
TRON_ENDPOINT=https://nile.trongrid.io
TRON_API_KEY=<free TronGrid key>
TRON_WALLET_ADDRESS=<Nile address>
TRON_PRIVATE_KEY=<its private key>
TRON_ANCHOR_ADDRESS=<destination address for the anchor>
```

Nile is a **testnet**. Its faucet gives 1000 TRX free. Do not touch mainnet, do
not spend real TRX, and do not commit a key. If a key ever appears in a diff,
stop and say so.

### The design, already decided

From `vitruvyan/mercator`, `.github/Vitruvyan_Appendix_H_Blockchain_Ledger.md`:

- **off-chain processing, on-chain anchoring only** — the trace stays in the
  database; only a digest goes to the chain;
- the digest travels in the transaction's **memo field** (100 char limit), so
  there is no smart contract to write or audit;
- transfers are minimal — 1 SUN;
- `tronpy==0.4.0` is the pinned client.

Use the memo prefix already chosen there: `VITRUVYAN_AUDIT:<root>`.

### What to build

Two functions, and resist adding a third:

```python
def anchor(root: str) -> dict:
    """Publish one trace root. Returns the receipt: txid, network, timestamp."""

def verify(root: str, receipt: dict) -> bool:
    """Re-read the transaction from the chain and confirm it carries this root."""
```

Store the receipt beside the trace. `verify` is the half people forget and the
only half that proves anything.

### Batching — later, not now

Appendix H proposes 100 events per anchor via a Merkle root. **Do not build
that yet.** Anchor one verdict, verify it, and see it on
`https://nile.tronscan.org`. A batching scheme whose single-item case was never
made to work is a scheme nobody can debug.

### Verify

0. Assert the memo fits before you send. `VITRUVYAN_AUDIT:` is 16 characters
   and a root is **71** — `sha256:` plus 64 hex — so **87** of the 100
   allowed. Make that an assertion in `anchor()`, not an assumption in this
   document.

   **Publish the root whole, prefix included.** The one anchor Vitruvyan has
   published carries the 64 hex characters with the algorithm stripped, which
   leaves a verifier in 2034 guessing which function to recompute. Nothing
   forced it — 87 fits — and code that accepts only 64 characters will either
   reject `trace.root` outright or quietly strip the part that makes it
   identifiable.
1. Anchor a real verdict trace's root. Record the txid.
2. Open the transaction on Nile Tronscan and read the memo with your own eyes.
3. Call `verify()` and get `True`.
4. Change one byte of the stored trace, recompute its root, call `verify()`
   with the new root: it must return `False`.

Step 4 is the entire point. Without it you have a transaction, not evidence.

---

## Reporting

At the end of each phase, report:

- what changed, file by file;
- what you verified and the actual output, not a claim that it passed;
- anything that did not behave as this prompt predicted — especially in Phase 1,
  where a behaviour difference is a Motus bug and must be reported upstream to
  `vitruvyan/motus` rather than worked around here.

Do not proceed past a phase whose verification did not pass.
