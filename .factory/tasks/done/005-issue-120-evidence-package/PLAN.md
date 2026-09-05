# Architecture plan — #120, the Evidence Package

Refines the CTO decisions in `.factory/tasks/005-issue-120-evidence-package.md`
into buildable module layout, algorithms and tests. Does not reopen any
numbered CTO decision; where a decision leaves a gap (it leaves two), the gap
is closed here and flagged as a plan-level choice, not a CTO one.

## 0. The one fact that shapes everything else

**`src/vitruvyan_motus/__init__.py` must stay stdlib-only on bare `import
vitruvyan_motus`.** `tests/test_motus_packaging.py::test_importing_motus_pulls_no_third_party_module`
proves this against a running interpreter, and `graph.py`'s own docstring
states the kernel "does not import `contract/validate.py`" — because that
file imports `jsonschema`, the distribution's one declared dependency, used
only by the shipped validator. `contract/` maps to `vitruvyan_motus.contract`
(`pyproject.toml`) but nothing in the kernel imports it at module scope today.

Decision 3 requires `evidence.py` to export `pack`/`verify_package` from
`vitruvyan_motus.__all__`, AND requires `verify_package`'s `PackageVerdict` to
carry "the ADR-020 verdict from `verify()`" — i.e. `contract/validate.py`'s
`verify()`. Doing both without breaking the stdlib-only kernel means:

**`evidence.py` imports `vitruvyan_motus.contract.validate` and reads
`vitruvyan_motus.__version__` only *inside function bodies*, never at module
scope.** `import vitruvyan_motus.evidence` (triggered by `__init__.py`'s
top-level `from vitruvyan_motus.evidence import pack, verify_package,
PackageVerdict`) must not itself import `jsonschema`; only *calling*
`verify_package(...)` does. This is exactly the existing philosophy — the
kernel is importable without `jsonschema`, using it costs you the one declared
dependency, same as `motus-validate` already does.

This must be verified, not assumed: after wiring, run
`test_importing_motus_pulls_no_third_party_module` (motus-verifier can do
this as part of the ordinary suite run — it is already in
`tests/test_motus_packaging.py`) and confirm it still passes.

Everything else evidence.py needs at module scope IS safe: `CommitmentLog`
(commitlog.py — stdlib + intra-package only, confirmed by reading its
imports), `TraceBundle` (replay.py — stdlib + intra-package only),
`AnchorReceipt` (commitments.py — stdlib only). None of these currently ship
in `__init__.py`'s import list (`commitlog.py` is not imported there today —
it is deliberately opt-in per ADR-021 decision 1, reached only via
`from vitruvyan_motus.commitlog import CommitmentLog`), but importing them
transitively through `evidence.py` does not violate the stdlib-only property,
only the jsonschema-free property matters and is what needs guarding.

## 1. New file: `src/vitruvyan_motus/evidence.py`

```python
"""The evidence package: a zip a stranger can verify holding only bytes.

... (module docstring states decision 1 verbatim: no fifth identity)
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from vitruvyan_motus.commitlog import CommitmentLog
from vitruvyan_motus.commitments import AnchorReceipt
from vitruvyan_motus.replay import TraceBundle
from vitruvyan_motus.trace import _canonical_bytes

__all__ = ["pack", "verify_package", "PackageVerdict"]

_PACKAGE_VERSION = "1.0"
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)   # fixed; decision 2's "deterministic zip"
_INTEGRITY_NOTE = (
    "manifest digests detect transport damage; execution integrity is "
    "derived from trace.json, never from this file"
)
_NO_LOG_NOTE = "no commitment log: no receipt"
```

### 1.1 `PackageVerdict`

```python
@dataclass(frozen=True, slots=True)
class PackageVerdict:
    """What `verify_package` could establish, holding only the package bytes.

    `verdict` is the ADR-020 `Verdict` from `contract.validate.verify()` when
    `core/receipt.json` is a member of the package, and `None` when it is not
    — a package built without a `CommitmentLog` (decision 2's third layout
    case) never claimed a receipt, and `None` says exactly that rather than
    manufacturing a Verdict about a document that was never included.

    `transport_ok` and `damaged` are independent of `verdict` and are ALWAYS
    computed from the actual bytes present, whatever they are: decision 4's
    acceptance test requires the two questions ("did the bytes survive
    transport" and "does the evidence establish anything") to be answered
    separately even when an attacker has edited BOTH the tampered file and the
    manifest entry that describes it, and this dataclass exists so a caller
    cannot conflate them by construction.
    """
    verdict: "Verdict | None"     # vitruvyan_motus.contract.validate.Verdict
    transport_ok: bool
    damaged: tuple[str, ...]
```

Note on the type annotation: `Verdict` is `contract.validate.Verdict`, a
`jsonschema`-adjacent type only reachable through a lazy import. Use a
`TYPE_CHECKING`-guarded import for the annotation so static tools resolve it
without the runtime module-scope import:

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Verdict
```

(`from __future__ import annotations` is already in force, so the
string-vs-object distinction above is cosmetic; keep the `TYPE_CHECKING`
guard anyway — it is the standard way to document the type without the
runtime cost, and matches how a reader would expect to find it.)

### 1.2 `pack`

```python
def pack(
    bundle: TraceBundle,
    *,
    log: CommitmentLog | None = None,
    anchors: Iterable[AnchorReceipt] = (),
    proofs: Mapping[str, bytes] = {},
    attachments: Mapping[str, bytes] = {},
) -> bytes:
```

Algorithm:

1. `trace_dict = bundle.trace.to_dict()`; `graph_dict = bundle.spec.to_dict()`
   — `TraceBundle.__post_init__` already ran `_assert_bundle_semantics`, so
   spec/trace agreement is a precondition, not something `pack` re-checks.

2. **Resolve the receipt, if a log was given:**
   ```python
   if log is not None:
       run_id = bundle.trace.run["run_id"]
       execution_ref = log.find_execution_ref(run_id)   # new method, §2
       receipt = log.receipt_for(execution_ref, anchors=anchors)
       execution_block = dict(receipt["execution"])
   else:
       receipt = None
       execution_block = {
           "ref": None,
           "fingerprint": bundle.trace.root,   # kernel-native, ADR-019; None
                                                # for an unfinished/rootless trace
           "run_id": trace_dict["run"]["run_id"],
       }
   ```
   `find_execution_ref` raises (`ValueError` / `CommitmentLogFork`) if the
   log holds no sealed BEGIN for this run_id, more than one (an unresolved
   ambiguity — a retried run_id with more than one sealed segment; `pack`'s
   fixed signature has no way to disambiguate and this is the documented,
   honest failure: the caller opens a narrower log or reopens a different
   `CommitmentLog` view). `receipt_for` itself raises if the matched BEGIN
   is not yet sealed ("is not yet sealed") — packaging an execution whose
   window has not been sealed is refused, not guessed at.

3. **Assemble members** (name → bytes), sanitising every caller-supplied key
   in `proofs`/`attachments` through the SAME `_is_safe_member_name` check
   `verify_package` uses on read (§1.3) — `pack` fully controls the fixed
   member names, but `proofs`/`attachments` keys come from the embedder and
   may be built from tenant- or user-controlled values (e.g. a checkpoint
   digest is safe, but nothing stops a caller from passing something else);
   refusing an unsafe key here is cheap defense-in-depth, not required by
   the acceptance test but consistent with "build the general solution":

   ```python
   members: list[tuple[str, bytes]] = [
       ("core/trace.json", _canonical_bytes(trace_dict)),
       ("core/graphspec.json", _canonical_bytes(graph_dict)),
   ]
   if receipt is not None:
       members.append(("core/receipt.json", _canonical_bytes(receipt)))
   for name, payload in proofs.items():
       _require_safe_key(name)
       members.append((f"core/proofs/{name}", payload))
   for name, payload in attachments.items():
       _require_safe_key(name)
       members.append((f"supplementary/attachments/{name}", payload))
   ```

4. **Build the manifest** (never includes an entry for itself — decision 1,
   no fifth identity):

   ```python
   from vitruvyan_motus import __version__ as _motus_version   # lazy, §0

   files = [
       {
           "name": name,
           "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
           "section": name.split("/", 1)[0] if "/" not in name[5:] else
                      "/".join(name.split("/")[:2]),   # "core" | "core/proofs"
                                                         # | "supplementary/attachments"
       }
       for name, payload in members
   ]
   manifest: dict[str, Any] = {
       "package_version": _PACKAGE_VERSION,
       "motus_version": _motus_version,
       "execution": execution_block,
       "files": sorted(files, key=lambda f: f["name"]),
       "packed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
       "integrity": _INTEGRITY_NOTE,
   }
   if receipt is None:
       manifest["note"] = _NO_LOG_NOTE
   members.append(("manifest.json", _canonical_bytes(manifest)))
   ```

   `packed_at` is real wall-clock content (like a commitment's `at`), not
   container metadata — two packings of the identical bundle at different
   times legitimately produce different bytes because of this field. That is
   a different property from "deterministic zip" (below), which is about
   the ZIP FORMAT's own incidental per-entry metadata never leaking
   irrelevant noise (host OS, real mtimes) — not about byte-for-byte
   reproducibility across time. State this distinction in the docstring so
   nobody writes a test asserting two `pack()` calls a second apart produce
   identical bytes.

5. **Write the deterministic zip:**

   ```python
   buffer = io.BytesIO()
   with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
       for name, payload in sorted(members, key=lambda m: m[0]):
           info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
           info.external_attr = 0o644 << 16
           info.create_system = 0        # do not leak the packing host's OS
           archive.writestr(info, payload)
   return buffer.getvalue()
   ```

   Sorted names, fixed `date_time`, fixed `external_attr`, fixed
   `create_system` — matches decision 2's "sorted names, fixed timestamps,
   no extra attributes" and the existing convention in
   `demo/bundle_hiring.py`/`demo/bundle_scenarios.py` (`ZIP_DATE`,
   `external_attr = 0o644 << 16`), generalised. `create_system = 0` is new
   relative to the demo prototypes — `zipfile.ZipInfo` otherwise stamps the
   creating host's `sys.platform`-derived value, which the demos never
   noticed because they always ran on the same CI/dev OS; a genuinely
   deterministic artifact must not vary with the packer's OS either.

### 1.3 `verify_package`

```python
def verify_package(data: bytes) -> PackageVerdict:
```

Must work from `data: bytes` alone — no paths, no env, no network (decision
3). Must never raise on adversarial input; a malformed/hostile package is a
*result* (`PackageVerdict`), not an exception — this mirrors
`contract.validate.verify()`'s own "refuses rather than guesses" rather than
crashing the second machine the adversarial round's lens 1 asks for. The one
exception is `data` not being `bytes` at all (a caller bug, not an
adversarial package) — let that `TypeError` propagate as it would from any
other Motus entry point.

Algorithm:

1. **Open the zip; refuse a package that is not one:**
   ```python
   try:
       archive = zipfile.ZipFile(io.BytesIO(data))
   except zipfile.BadZipFile:
       return PackageVerdict(None, False, ("<not a zip file>",))
   names = archive.namelist()
   ```

2. **Zip-slip, refused before anything is read — decision 4:**
   ```python
   unsafe = [n for n in names if not _is_safe_member_name(n)]
   if unsafe:
       return PackageVerdict(
           None, False,
           tuple(f"<unsafe member name>: {n}" for n in unsafe),
       )
   ```
   `_is_safe_member_name` refuses: a leading `/` or `\`, a Windows drive
   prefix (`C:\...`), and any path segment that is `""`, `"."` or `".."`.
   `verify_package` never calls `ZipFile.extract`/`extractall` — it only
   ever calls `.read(name)` into memory, so on THIS implementation zip-slip
   cannot touch a filesystem regardless. The check stands anyway and is
   documented as such in the docstring, exactly as decision 4 asks
   ("verify_package writes nothing anyway — say so"): a package carrying
   such a name is refused on principle, not merely rendered harmless by
   accident of this implementation.

3. **Load the manifest; a missing or unparsable one is total refusal:**
   ```python
   if "manifest.json" not in names:
       return PackageVerdict(None, False, ("<manifest.json is missing>",))
   try:
       manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
   except (ValueError, UnicodeDecodeError):
       return PackageVerdict(None, False, ("manifest.json: not valid JSON",))
   ```

4. **Recompute every declared digest — this is the ONLY thing manifest.json
   is trusted for (decision 1), and it is never trusted for validity, only
   for "did these bytes survive transport":**
   ```python
   files = manifest.get("files")
   damaged: list[str] = []
   if not isinstance(files, list):
       damaged.append("manifest.json: no file list")
   else:
       for entry in files:
           name = entry.get("name") if isinstance(entry, dict) else None
           expected = entry.get("sha256") if isinstance(entry, dict) else None
           if not isinstance(name, str) or not isinstance(expected, str):
               damaged.append(f"<malformed manifest entry>: {entry!r}")
               continue
           if name not in names:
               damaged.append(name)          # declared, absent: truncated download
               continue
           actual = "sha256:" + hashlib.sha256(archive.read(name)).hexdigest()
           if actual != expected:
               damaged.append(name)          # declared, present, wrong bytes
   transport_ok = not damaged
   ```
   **A member present in the zip but not declared in `manifest["files"]` is
   never flagged.** Considered and rejected: such a file cannot affect
   `verdict` (only `core/trace.json` and `core/receipt.json` are ever read
   for that, by fixed path, never by walking the manifest's file list) and
   flagging it would mean trusting the manifest to say what a "complete"
   package looks like — exactly the fifth-identity trust decision 1
   forbids. An attacker who smuggles in an extra file gains nothing: it is
   inert to every check `verify_package` performs.

5. **Read `core/trace.json` and, if present, `core/receipt.json`, by FIXED
   PATH — never by consulting the manifest's file list to find them.** This
   is the load-bearing move for the "manifest as a fifth identity" lens: the
   two documents that decide `verdict` are located structurally, by the
   package layout decision 2 fixes, not by trusting anything the manifest
   says about itself.
   ```python
   def _read_json(name: str) -> Any | None:
       if name not in names:
           return None
       try:
           return json.loads(archive.read(name).decode("utf-8"))
       except (ValueError, UnicodeDecodeError):
           return None

   trace_doc = _read_json("core/trace.json")
   if "core/receipt.json" in names:
       receipt_doc = _read_json("core/receipt.json")
       from vitruvyan_motus.contract.validate import verify as _contract_verify
       verdict = _contract_verify(
           receipt_doc if isinstance(receipt_doc, dict) else {}, trace_doc,
       )
   else:
       verdict = None
   return PackageVerdict(verdict, transport_ok, tuple(damaged))
   ```
   Passing `{}` (not `None`) when `core/receipt.json` fails to parse keeps
   the call type-correct against `verify(receipt: dict, ...)`; `verify()`'s
   own `isinstance(receipt, dict)` gate together with `validate_receipt({})`
   already produces "this document is not a receipt this validator can
   read" / NOT_ESTABLISHED on every level for `{}`, so behaviour is
   identical to what a bare `None` would produce, without touching the
   contract module's typed signature.

   **Why this achieves decision 4's "manifest recomputed by the attacker
   must still fail on the receipt comparison":** an attacker who edits
   `core/trace.json` and recomputes its manifest digest to match produces
   `transport_ok=True` (no digest disagrees with the bytes actually
   present) — but `verify_package` still reads the ACTUAL (tampered)
   `core/trace.json` bytes and the UNTOUCHED `core/receipt.json` bytes and
   calls `contract.validate.verify()` on that real pair. INTEGRITY drops
   because the recomputed root no longer equals the receipt's END root
   (`contract/validate.py`'s existing INTEGRITY comparison) and rule P8
   independently catches `execution.fingerprint` disagreeing with the
   re-derived root. Forging the manifest changes nothing here because the
   manifest is never consulted for this comparison — only used for the
   separate, honestly-labelled `transport_ok` signal.

   A single byte changed **inside one of manifest.json's own digest
   strings** (rather than inside a real evidentiary file) is exactly the
   "tamper the manifest" case in decision 4's five-file list: it corrupts
   the recorded digest for whichever file that entry describes (say
   `core/trace.json`), so step 4 reports `"core/trace.json"` as damaged —
   not `"manifest.json"` itself, which has no digest of its own to compare
   against (decision 1: no fifth identity). This must be stated explicitly
   in the test and in evidence.py's docstring so a reader does not expect a
   `"manifest.json"` entry to ever appear in `damaged` for this reason.

### 1.4 Helpers

```python
def _is_safe_member_name(name: str) -> bool: ...
def _require_safe_key(name: str) -> None:     # raises ValueError; used by pack()
```

Both live in `evidence.py`, private (`_`-prefixed), shared between `pack`'s
input hygiene and `verify_package`'s zip-slip refusal — one definition, two
callers, matching AGENTS.md's "repair the class" habit (a single member-name
safety rule, not two).

## 2. New method: `CommitmentLog.find_execution_ref` (`commitlog.py`)

**The gap decision 3 leaves open:** `pack(bundle, *, log=None, ...)` has no
`execution_ref` parameter, yet `receipt_for` requires one. `pack` must derive
it from `bundle.trace.run["run_id"]` and the given `log`. No existing public
method does this — `receipt_for` takes a ref, `proof_for` takes a
`(run_id, kind, checkpoint_index)` (the caller must already know which
window), and `_continuation` is private and answers a narrower question
(unpaired BEGINs only, for resume). This is new code, not a reopened CTO
decision — decision 3 fixed `pack`'s signature, not how it is implemented.

```python
def find_execution_ref(self, run_id: str, *, sequence: int | None = None) -> str:
    """The ADR-027 execution_ref of the sealed BEGIN for `run_id` in this chain.

    A run_id may repeat (a retried job keeping its id, ADR-021/023), so more
    than one sealed BEGIN can share it; `sequence` breaks the tie exactly as
    `proof_for` and `_continuation` already require it to be broken
    elsewhere in this file — picking one without being told would be a
    guess wearing a coordinate's authority.

    Only SEALED windows are searched, matching `receipt_for`: an execution
    whose window has not been sealed cannot be proved and is reported as not
    found here too, for the same reason.
    """
    candidates: list[Commitment] = []
    for index in self._checkpoint_indices():
        _, commitments = self._sealed_window(index)
        candidates.extend(
            c for c in commitments
            if c.kind is CommitmentKind.BEGIN and c.run_id == run_id
            and (sequence is None or c.sequence == sequence)
        )
    if not candidates:
        raise ValueError(
            f"no sealed BEGIN for run {run_id!r} in "
            f"{self.tenant}/{self.writer_id}"
            + ("" if sequence is None else f" at sequence {sequence}"))
    if len(candidates) > 1:
        available = ", ".join(str(c.sequence) for c in candidates)
        raise CommitmentLogFork(
            f"{len(candidates)} sealed BEGINs share run {run_id!r} in this "
            f"chain (sequences {available}); naming one would be a guess. "
            "Pass sequence=<n>")
    chosen = candidates[0]
    return f"{chosen.tenant}/{chosen.writer_id}/{chosen.sequence}"
```

Add `find_execution_ref` to no `__all__` list changes are needed —
`CommitmentLog` itself is not (and should not become) part of
`vitruvyan_motus.__all__`; it stays reached via
`from vitruvyan_motus.commitlog import CommitmentLog`, unchanged.

**Consequence for `pack()`:** packaging a just-finished run requires the
caller to have called `log.seal()` on the window holding its BEGIN first.
This is not new — `receipt_for` already has this precondition — `pack`
simply inherits it and the resulting `ValueError`/`CommitmentLogFork`
propagates unchanged. State this in `pack`'s docstring.

## 3. `src/vitruvyan_motus/__init__.py`

```python
from vitruvyan_motus.evidence import PackageVerdict, pack, verify_package
...
__all__ = [
    ...
    "TraceBundle", "ReplayResult", "ReplayEngine",
    "pack", "verify_package", "PackageVerdict",
    ...
]
```

Placed near the replay exports (evidence packaging is "what you do with a
`TraceBundle`", the same neighbourhood). This is the change
`test_importing_motus_pulls_no_third_party_module` and
`test_the_readme_names_every_public_symbol` (in `tests/test_examples.py`)
will exercise automatically — no new test needed for the second one, only
the README edit in §5.

## 4. `contract/validate.py` — CLI wiring

**Constraint:** `contract/validate.py` must stay independently runnable
(`python contract/validate.py ...`) without depending on `vitruvyan_motus` at
module scope — it is the authority; the kernel depends on it, never the
reverse (AGENTS.md's authority order). The one exception, scoped as narrowly
as possible: the CLI dispatcher's `package` branch, which by definition needs
`evidence.verify_package`, imports it **lazily, inside that branch only**,
mirroring the existing lazy `--trace` file load already in `main()`.

Two structural changes to `main()`:

1. **Add `"package"` to the `artifact` choices**, and refuse
   `--spec`/`--trace`/`--allow-incomplete` combined with it (extend the
   existing two `parser.error(...)` guards).

2. **Branch on `package` BEFORE the unconditional
   `raw = Path(args.file).read_bytes().decode("utf-8")`** near the top of
   `main()` — a package is a binary zip, not UTF-8 text, and that line
   would raise `UnicodeDecodeError` → exit 2 ("not valid UTF-8") for every
   package, which is wrong. Structure:

   ```python
   args = parser.parse_args(argv)
   if args.artifact not in ("trace", "jsonl") and (args.spec or args.allow_incomplete):
       parser.error("--spec and --allow-incomplete apply to trace/jsonl only")
   if args.trace and args.artifact != "receipt":
       parser.error("--trace applies to receipt only")

   if args.artifact == "package":
       from vitruvyan_motus.evidence import verify_package
       try:
           data = Path(args.file).read_bytes()
       except OSError as exc:
           print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
           return 2
       pv = verify_package(data)
       lines = [f"transport_ok: {pv.transport_ok}"]
       if pv.damaged:
           lines.append("damaged: " + ", ".join(pv.damaged))
       if pv.verdict is None:
           lines.append(
               "no receipt in this package: nothing beyond transport "
               "integrity was checked"
           )
       else:
           lines.append(format_verdict(pv.verdict))
       print("\n".join(lines))
       bad = bool(pv.damaged) or (
           pv.verdict is not None and (pv.verdict.violations or pv.verdict.refused)
       )
       return 1 if bad else 0

   try:
       raw = Path(args.file).read_bytes().decode("utf-8")
   ...
   ```

**Exit-code rationale (a plan-level choice, stated explicitly since decision
3/4 do not spell it out):** `pv.verdict is None` alone does NOT cause exit 1
— a package with no receipt is a legitimate, documented layout (decision 2's
third case), not a failure. Only actual transport damage, or a present
receipt that fails/refuses, is exit 1. This mirrors the existing "a refusal
outranks a violation, but absence-of-a-claim is not a violation" posture of
`verify()` itself.

## 5. README

Two edits, both additive, no restructuring:

1. **"Native package surface"** (§917) and the `__all__` prose bullet list —
   add a new bullet, e.g. after the `replay:` bullet:
   ```
   - evidence packaging: `pack`, `verify_package`, `PackageVerdict`;
   ```
   This satisfies `test_the_readme_names_every_public_symbol` (each name
   must appear verbatim somewhere in the README; the bullet list is the
   natural, already-established place other symbols were added).

2. **The "Verification is open and stays open" paragraph** (§877–889, the
   closest match to the brief's "verify from a clean directory" paragraph —
   no section is literally titled that; this is the one that states the
   property: offline, no account, no server of ours). Add one line after the
   existing `motus-validate receipt ...` example:
   ```
   motus-validate package path/to/evidence.zip
   ```
   with a one-sentence lead-in, e.g. "A packaged bundle (`evidence.py`,
   `pack`/`verify_package`) is checked the same way, from the zip alone:".

No change to `### The root, for whoever anchors it` or the
`demo/anchor_domains.py` `ots verify` flow — those are unrelated to the
evidence package (they document anchor verification, not receipt/package
verification) and decision 5 does not ask for changes there.

## 6. Demo migration (decision 5)

`demo/bundle_hiring.py` and `demo/bundle_scenarios.py` currently
hand-assemble a **different, older zip shape** (`VERIFY.txt`, `trace.json`,
`graph.json`, `explain.json`, `anchor.json`, `root.txt`, `root.txt.ots`,
`derive_root.py`, `bundle.json`) — this is NOT the evidence-package layout
decision 2 defines (no `manifest.json`, no `core/`/`supplementary/` split,
no receipt). Decision 5 says these two scripts should "call `pack()` instead
of hand-assembling" — read together with "`demo/out/` artefacts are NOT
regenerated (they are frozen evidence of the prior format)", the intent is:

**Only the zip-writing step changes; the bundle's own file layout (the
`VERIFY.txt`-narrated, OpenTimestamps-flavoured shape) is untouched, and
`demo/out/*.motus.zip` files already committed are never rebuilt or
diffed against.** Concretely, replace the two scripts' own
`zipfile.ZipFile(...) as archive: ... archive.writestr(...)` loops with a
call into a **shared helper that applies decision 2's determinism rules**
(sorted names, fixed date, fixed external_attr) — but NOT `evidence.pack()`
itself, because `pack()`'s member list is fixed by decision 2 to
`manifest.json`/`core/*`/`supplementary/*`, and these demos' members
(`VERIFY.txt`, `root.txt.ots`, ...) do not fit that shape and are not a
`TraceBundle` + `CommitmentLog` in the first place (they are built from
already-serialized `demo/out/**/*.json` files on disk, no `Runtime` object
in scope).

**Plan-level call:** extract the deterministic-zip-writing loop
(`ZIP_DATE`, `external_attr = 0o644 << 16`, sorted member order) that both
demo scripts already duplicate into a small shared helper —
`_write_deterministic_zip(path, members)` — living in `bundle_hiring.py`
(already the module `bundle_scenarios.py` imports `ZIP_DATE`/`DERIVE` from)
and imported by `bundle_scenarios.py` the same way. This satisfies decision
5's spirit (stop hand-duplicating the zip-writing logic; centralise it) and
keeps `evidence.pack()` scoped to what decision 2 actually defines, without
either widening `pack()`'s fixed layout to accommodate the demo's unrelated
shape or leaving the demo's duplicated zip-writing loop untouched. Flag this
reading to the founder/reviewer explicitly in the REPORT — it is the one
place decision 5's wording ("call `pack()` instead of hand-assembling") could
be read more literally, and the literal reading does not typecheck against
decision 2's fixed layout, so this is the resolution, not an oversight.

Add one new demo (or extend an existing one, e.g. a small new
`demo/pack_scenario.py` or a `--evidence-package` step folded into
`three_domains.py`) is **not required** by decision 5 and is out of scope —
decision 5 only names the two existing scripts.

## 7. Tests — new file `tests/test_evidence_package.py`

(Not `test_motus_evidence.py` — that file already exists, covers durable-sink
behaviour, and is unrelated; reusing its name would collide two unrelated
concerns under one filename.)

Coverage, mapped to decision 4's acceptance and the two adversarial lenses:

1. **Round trip, no log.** `pack(bundle)` → `verify_package(data)` →
   `transport_ok=True`, `damaged=()`, `verdict is None`.
2. **Round trip, with log — completed execution.** Build a `CommitmentLog`
   in `tmp_path`, `begin()`/`end()`/`seal()`, `pack(bundle, log=log)` →
   `verify_package` → `verdict.status_of("INTEGRITY") == ESTABLISHED`.
   Do this **in a fresh `tmp_path` holding only the returned `bytes`** — no
   filesystem paths from the packing side reused (decision 4: "in a fresh
   `tmp_path` with only the bytes").
3. **Brutal, per decision 4** — build once, then for each of `trace`,
   `graphspec`, `receipt`, `manifest` (a digest string inside it), and a
   `proof`: flip one byte, re-verify, assert:
   - the correct file name(s) land in `damaged` (for the "manifest" case,
     assert the file the corrupted digest *describes* appears — NOT
     `"manifest.json"`, per §1.3's precision note);
   - for trace/receipt edits, `verdict.status_of("INTEGRITY")` (or the
     relevant level) is no longer `ESTABLISHED`;
   - **the manifest-forged variant**: tamper `core/trace.json`, then
     recompute and REPLACE `manifest.json`'s entry for it so digests agree
     (simulating an attacker who edits both) — assert `transport_ok is True`
     for that file AND the verdict still drops. This is the single test that
     proves lens 2 (manifest is not a fifth identity).
4. **Unfinished execution.** `begin()` + `seal()`, no `end()`; pack the
   in-flight `TraceBundle`; `verify_package` returns a real `Verdict` whose
   `notes` contain the "AN EXECUTION THAT LEFT NO COMPLETION" text `verify()`
   already produces (assert the note is present, do not re-assert its
   wording verbatim beyond a substring — that text belongs to
   `contract/validate.py`, not to this test).
5. **Resumed execution.** Two segments under one log (`ReplayEngine.resume`
   fixture, or a `CommitmentLog.begin(..., continues=..., continues_fingerprint=...)`
   built directly); pack the SECOND segment's bundle; assert the receipt
   covers both segments and the multi-segment continuation note appears.
6. **Zip-slip.** Hand-build a zip (not via `pack()`) containing a member
   named `../evil.json` or `/etc/passwd`; `verify_package` returns
   `verdict=None, transport_ok=False`, with the unsafe name surfaced in
   `damaged`, and — this is the assertion that actually matters — no
   file is ever written to disk (the test can assert this trivially, since
   `verify_package` never touches `tmp_path` at all; consider asserting via
   `os.listdir` unchanged, or simply by code inspection/coverage that no
   `extract`/`extractall`/`open(..., "w")` call exists in `evidence.py`).
7. **Bad zip / missing manifest / unparsable manifest / manifest with a
   non-list `files`** — each returns a clean `PackageVerdict`, never raises.
8. **`find_execution_ref`** (in `tests/test_commitlog.py`, alongside the
   existing coverage, not in the new file — it is a `CommitmentLog` method):
   no sealed BEGIN → `ValueError`; two sealed BEGINs sharing a retried
   `run_id` → `CommitmentLogFork` naming both sequences, resolved by
   `sequence=`; exactly one → correct `tenant/writer_id/sequence` string.
9. **CLI.** `motus-validate package <file>` on a good package (exit 0,
   `transport_ok: True` on stdout), a damaged one (exit 1), and a
   no-receipt one (exit 0, "no receipt in this package" on stdout) —
   subprocess-based, matching the existing CLI test style used for `receipt`
   (grep `tests/` for the existing `motus-validate receipt` subprocess test
   and mirror its harness).
10. **README/public-surface** — no new test needed; `test_examples.py`'s
    existing `test_the_readme_names_every_public_symbol` covers it once §3/§5
    land.
11. **Packaging/import-graph regression** — no new test needed;
    `test_motus_packaging.py::test_importing_motus_pulls_no_third_party_module`
    covers it once §3 lands, PROVIDED §1's lazy-import discipline is
    actually followed. This is the single highest-value thing for
    motus-verifier to check first after the implementer's pass, since a
    module-level `import jsonschema` (direct or via
    `vitruvyan_motus.contract.validate`) anywhere in the new code's import
    chain fails it silently otherwise (the failure surfaces as a subprocess
    stdout diff, not a normal traceback).

## 8. What is explicitly NOT in scope

- No change to `contract/receipt.v1.schema.json` or any frozen path — the
  `execution` object ADR-027 adds is already produced by
  `CommitmentLog.receipt_for` (landed with #118) and already checked by
  `contract/validate.py`'s P8 rule; the evidence package consumes both,
  changes neither.
- No change to `contract/validate.py`'s `verify()` semantics — `evidence.py`
  calls it, does not wrap, filter or reinterpret its findings.
- No SLO/benchmark changes — `pack`/`verify_package` are not on the
  `Runtime`/`ReplayEngine` run path (decision 6).
- No new demo script beyond the two decision 5 names.
- `CommitmentLog` is not added to `vitruvyan_motus.__all__` — only
  `pack`/`verify_package`/`PackageVerdict` are, per decision 3.
