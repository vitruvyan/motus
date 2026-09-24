"""A transport envelope for evidence, not a fifth identity.

The manifest's per-file digests detect transport damage only. Execution
integrity is always re-derived from ``core/trace.json`` and checked against the
receipt; the manifest is never used to establish validity and has no digest of
its own. ``verify_package`` reads zip members into memory and writes nothing,
including when refusing zip-slip names. ZIP metadata is deterministic, while
``packed_at`` intentionally changes between packings.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from vitruvyan_motus._execution_ref import (
    receipt_segment_for_execution_ref, receipt_terminal_segment,
)

if TYPE_CHECKING:
    from vitruvyan_motus.commitlog import CommitmentLog
    from vitruvyan_motus.commitments import AnchorReceipt, Attestation
    from vitruvyan_motus.replay import TraceBundle
    from vitruvyan_motus.contract.validate import Verdict

__all__ = [
    "pack", "verify_package", "evidence_package_fingerprint", "PackageVerdict",
]

_PACKAGE_VERSION = "1.0"
_MANIFEST_MEMBER = "manifest.json"
_RECEIPT_MEMBER = "core/receipt.json"
_SHA256_PREFIX = "sha256:"
_SHA256_HEX_LENGTH = 64
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)
_INTEGRITY_NOTE = (
    "manifest digests detect transport damage; execution integrity is "
    "derived from trace.json, never from this file"
)
_NO_LOG_NOTE = "no commitment log: no receipt"
# Fixed JSON is contract input. 28 MiB accommodates the measured 27.4 MiB
# medium trace while staying below the tested astral-string failure threshold.
_FIXED_MEMBER_MAX_BYTES = 28 * 1024 * 1024
_FIXED_JSON_MEMBERS = frozenset({
    _MANIFEST_MEMBER, "core/trace.json", "core/graphspec.json", _RECEIPT_MEMBER,
})


def _is_sha256_digest(value: object) -> bool:
    """Whether value is canonical Motus SHA-256 identifier text."""
    if not isinstance(value, str) or not value.startswith(_SHA256_PREFIX):
        return False
    body = value[len(_SHA256_PREFIX):]
    return (
        len(body) == _SHA256_HEX_LENGTH
        and all(char in "0123456789abcdef" for char in body)
    )


@dataclass(frozen=True, slots=True)
class PackageVerdict:
    """Independent transport, trace, and receipt results for a package."""
    verdict: "Verdict | None"
    transport_ok: bool
    damaged: tuple[str, ...]
    trace_violations: tuple[str, ...] = ()


def evidence_package_fingerprint(data: bytes) -> str:
    """Return the exact transport fingerprint of one evidence-package blob.

    This is an ADR-039 reference identity for the bytes held by a caller, not
    a replacement execution identity and not a package-verification verdict.
    ``verify_package`` remains authoritative for the package contents.
    """
    if not isinstance(data, bytes):
        raise TypeError("package data must be bytes")
    return _SHA256_PREFIX + hashlib.sha256(data).hexdigest()


def _is_safe_member_name(name: str) -> bool:
    if not isinstance(name, str) or not name or name.startswith(("/", "\\")):
        return False
    # Reject drive-qualified Windows paths even on POSIX.
    if len(name) >= 2 and name[0].isalpha() and name[1] == ":":
        return False
    parts = name.replace("\\", "/").split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _require_safe_key(name: str) -> None:
    if not _is_safe_member_name(name):
        raise ValueError(f"unsafe evidence member name: {name!r}")

def _archive_name_issues(
    names: Iterable[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return duplicate and unsafe member names under one package policy."""
    seen: set[str] = set()
    duplicates: list[str] = []
    duplicate_reported: set[str] = set()
    unsafe: list[str] = []
    for name in names:
        if name in seen and name not in duplicate_reported:
            duplicates.append(name)
            duplicate_reported.add(name)
        seen.add(name)
        if not _is_safe_member_name(name):
            unsafe.append(name)
    return tuple(duplicates), tuple(unsafe)


class _MemberReadError(Exception):
    """A ZIP member could not be read safely; the member name is retained."""

    def __init__(self, name: str, cause: BaseException) -> None:
        super().__init__(name)
        self.name = name
        self.cause = cause


def _read_member(
    archive: zipfile.ZipFile, name: str, *, limit: int | None = None,
) -> bytes:
    """Read a member with an optional decompressed-size bound."""
    try:
        if limit is None:
            return archive.read(name)
        with archive.open(name) as member:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = member.read(min(1024 * 1024, limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise _MemberReadError(
                        name, ValueError(f"decompressed member exceeds {limit} bytes"))
                chunks.append(chunk)
            return b"".join(chunks)
    except _MemberReadError:
        raise
    except (NotImplementedError, RuntimeError, zipfile.LargeZipFile,
            zipfile.BadZipFile, EOFError, OSError, MemoryError, zlib.error) as exc:
        raise _MemberReadError(name, exc) from None


def _digest_member(archive: zipfile.ZipFile, name: str) -> str:
    """Hash arbitrary attachments incrementally, preserving their size freedom."""
    digest = hashlib.sha256()
    try:
        with archive.open(name) as member:
            while chunk := member.read(1024 * 1024):
                digest.update(chunk)
    except (NotImplementedError, RuntimeError, zipfile.LargeZipFile,
            zipfile.BadZipFile, EOFError, OSError, MemoryError, zlib.error) as exc:
        raise _MemberReadError(name, exc) from None
    return _SHA256_PREFIX + digest.hexdigest()


def _strict_parse_error(
    exc: BaseException,
    strict_error: type[BaseException],
    noncanonical_number_error: type[BaseException],
) -> str | None:
    """Map strict-loader exceptions by class; ordinary parse errors stay generic."""
    if isinstance(exc, UnicodeDecodeError):
        return "not valid UTF-8"
    if not isinstance(exc, strict_error):
        return "not valid JSON"
    return "J2" if isinstance(exc, noncanonical_number_error) else "J1"


def pack(
    bundle: TraceBundle,
    *,
    log: CommitmentLog | None = None,
    execution_ref: str | None = None,
    anchors: Iterable[AnchorReceipt] = (),
    attestations: Iterable[Attestation] = (),
    proofs: Mapping[str, bytes] = {},
    attachments: Mapping[str, bytes] = {},
) -> bytes:
    """Pack a bundle into the fixed evidence layout.

    A commitment log must have sealed the execution window first. When no log
    is supplied the package honestly contains no receipt. When ``execution_ref``
    is supplied it selects the exact sealed BEGIN rather than resolving the run
    through ``run_id``; ADR-027 requires that distinction for retried ids.
    """
    from vitruvyan_motus.trace import _canonical_bytes
    trace_dict = bundle.trace.to_dict()
    graph_dict = bundle.spec.to_dict()
    if log is not None:
        from vitruvyan_motus.commitlog import CommitmentLog  # type: ignore[import-not-found]
        run_id = trace_dict["run"]["run_id"]
        selected_ref = (execution_ref if execution_ref is not None
                        else log.find_execution_ref(run_id))
        receipt = log.receipt_for(selected_ref, anchors=anchors,
                                  attestations=attestations)
        if execution_ref is not None:
            selected_segment = receipt_segment_for_execution_ref(receipt, selected_ref)
            if selected_segment is None:
                raise ValueError(
                    f"receipt does not contain requested execution {selected_ref!r}")

            # A receipt requested by any included BEGIN names the whole resumed
            # chain. The TraceBundle belongs to the terminal segment, not
            # necessarily to the BEGIN the caller used to locate that chain.
            terminal_segment = receipt_terminal_segment(receipt)
            terminal_begin = (terminal_segment.get("begin")
                              if isinstance(terminal_segment, dict) else None)
            terminal_commitment = (terminal_begin.get("commitment")
                                   if isinstance(terminal_begin, dict) else None)
            if (not isinstance(terminal_commitment, dict)
                    or terminal_commitment.get("run_id") != run_id):
                raise ValueError(
                    f"execution_ref {selected_ref!r} does not bind bundle run_id {run_id!r}")

            # The explicit coordinate is the new ADR-034 path. It must prove
            # the terminal END binds this trace root. The legacy
            # pack(bundle, log=...) path retains its pre-ADR-034 behavior and
            # leaves mismatch reporting to verify_package().
            trace_root = bundle.trace.root
            if trace_root is not None:
                end_entry = terminal_segment.get("end")
                end_commitment = (end_entry.get("commitment")
                                  if isinstance(end_entry, dict) else None)
                if (not isinstance(end_commitment, dict)
                        or end_commitment.get("root") != trace_root):
                    raise ValueError(
                        f"execution_ref {selected_ref!r} does not bind bundle root {trace_root!r}")
            else:
                # An unfinished trace has no root. If its terminal run_id is
                # repeated, nothing in the trace can distinguish which BEGIN
                # it belongs to; refuse rather than let the caller choose.
                terminal_tenant = terminal_commitment.get("tenant")
                terminal_writer = terminal_commitment.get("writer_id")
                terminal_sequence = terminal_commitment.get("sequence")
                if (
                    not isinstance(terminal_tenant, str)
                    or not isinstance(terminal_writer, str)
                    or type(terminal_sequence) is not int
                ):
                    raise ValueError(
                        f"execution_ref {selected_ref!r} has malformed terminal BEGIN identity")
                # Force the existing log ambiguity check. A unique run_id
                # necessarily resolves to this terminal BEGIN; repeated ids
                # raise CommitmentLogFork rather than reaching dead comparison
                # code with a second refusal message.
                log.find_execution_ref(run_id)
        execution = dict(receipt["execution"])
    else:
        if execution_ref is not None:
            raise ValueError("execution_ref requires a commitment log")
        receipt = None
        execution = {
            "ref": None,
            "fingerprint": bundle.trace.root,
            "run_id": trace_dict["run"]["run_id"],
        }

    members: list[tuple[str, bytes]] = [
        ("core/trace.json", _canonical_bytes(trace_dict)),
        ("core/graphspec.json", _canonical_bytes(graph_dict)),
    ]
    if receipt is not None:
        members.append((_RECEIPT_MEMBER, _canonical_bytes(receipt)))
    for name, payload in proofs.items():
        _require_safe_key(name)
        if not isinstance(payload, bytes):
            raise TypeError("proof payloads must be bytes")
        members.append((f"core/proofs/{name}", payload))
    for name, payload in attachments.items():
        _require_safe_key(name)
        if not isinstance(payload, bytes):
            raise TypeError("attachment payloads must be bytes")
        members.append((f"supplementary/attachments/{name}", payload))

    from vitruvyan_motus import __version__
    files = []
    for name, payload in members:
        if name.startswith("core/proofs/"):
            section = "core/proofs"
        elif name.startswith("supplementary/attachments/"):
            section = "supplementary/attachments"
        else:
            section = name.split("/", 1)[0]
        files.append({"name": name, "sha256": _SHA256_PREFIX + hashlib.sha256(payload).hexdigest(),
                      "section": section})
    manifest: dict[str, Any] = {
        "package_version": _PACKAGE_VERSION,
        "motus_version": __version__,
        "execution": execution,
        "files": sorted(files, key=lambda entry: entry["name"]),
        "packed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "integrity": _INTEGRITY_NOTE,
    }
    if receipt is None:
        manifest["note"] = _NO_LOG_NOTE
    members.append((_MANIFEST_MEMBER, _canonical_bytes(manifest)))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(members):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 0
            archive.writestr(info, payload)
    return buffer.getvalue()


def _identity_documents_from_package(
    data: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Strictly read manifest and receipt for consumer identity binding.

    This is narrower than ``verify_package``: it establishes what execution
    the package *labels* and what execution its receipt *contains*. It does
    not decide whether either artifact is valid.
    """
    if not isinstance(data, bytes):
        raise TypeError("package data must be bytes")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise ValueError("evidence package is not a zip file") from None
    try:
        names = archive.namelist()
        duplicate_names, unsafe = _archive_name_issues(names)
        if duplicate_names:
            raise ValueError("evidence package has duplicate member names")
        if unsafe:
            raise ValueError("evidence package has unsafe member names")
        for required in (_MANIFEST_MEMBER, _RECEIPT_MEMBER):
            if required not in names:
                raise ValueError(f"evidence package has no {required}")

        from vitruvyan_motus.contract.validate import _loads_strict

        def read_object(name: str) -> dict[str, Any]:
            try:
                value = _loads_strict(
                    _read_member(
                        archive, name, limit=_FIXED_MEMBER_MAX_BYTES
                    ).decode("utf-8"))
            except (
                _MemberReadError, UnicodeDecodeError, ValueError, RecursionError
            ) as exc:
                raise ValueError(
                    f"evidence package {name} is not readable strict JSON"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(f"evidence package {name} is not a JSON object")
            return value

        return read_object(_MANIFEST_MEMBER), read_object(_RECEIPT_MEMBER)
    finally:
        archive.close()


def verify_package(data: bytes) -> PackageVerdict:
    """Verify a package using only its bytes; malformed input becomes a result.

    ``core/trace.json`` is always sent through the contract validator.  Its
    findings are kept separate from manifest transport damage and from the
    receipt verdict: a package without a receipt still cannot silently turn a
    malformed trace into a clean result.  Such a package can establish only
    what the trace says about itself, not when it existed: it has no receipt,
    checkpoint, or anchor, and anybody holding the recipe can reseal an edited
    trace.  That is why the CLI reports ``EXISTENCE NOT ESTABLISHED`` rather
    than treating a clean trace as anchored evidence.
    """
    if not isinstance(data, bytes):
        raise TypeError("package data must be bytes")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return PackageVerdict(None, False, ("<not a zip file>",),
                              ("<trace unavailable: not a zip file>",))
    try:
        names = archive.namelist()
        duplicate_names, unsafe = _archive_name_issues(names)
        if duplicate_names:
            return PackageVerdict(
                None, False,
                tuple(f"<duplicate member>: {name}" for name in duplicate_names),
                ("<trace unavailable: duplicate physical member name>",),
            )
        if unsafe:
            return PackageVerdict(
                None, False,
                tuple(f"<unsafe member name>: {name}" for name in unsafe),
                ("<trace unavailable: unsafe member name>",),
            )
        if _MANIFEST_MEMBER not in names:
            return PackageVerdict(None, False, ("<manifest.json is missing>",),
                                  ("<trace unavailable: manifest is missing>",))
        # These imports are deliberately local: bare import of the kernel must
        # not pull jsonschema into the process.  The same strict loader is used
        # for every fixed-path JSON artifact, so parsing cannot launder a
        # duplicate member or a J2 number into an ordinary Python value.
        from vitruvyan_motus.contract.validate import (
            NonCanonicalNumberError, StrictJSONError, _loads_strict,
            validate_graphspec, validate_trace,
            verify,
        )

        try:
            manifest = _loads_strict(
                _read_member(
                    archive, _MANIFEST_MEMBER, limit=_FIXED_MEMBER_MAX_BYTES
                ).decode("utf-8"))
        except _MemberReadError as exc:
            return PackageVerdict(
                None, False,
                (f"{exc.name}: damaged/refused ({type(exc.cause).__name__})",),
                ("<trace unavailable: manifest could not be read>",))
        except RecursionError:
            return PackageVerdict(
                None, False,
                ("manifest.json: nesting beyond what this verifier can parse",),
                ("<trace unavailable: manifest nesting refused>",))
        except (ValueError, UnicodeDecodeError) as exc:
            rule = _strict_parse_error(
                exc, StrictJSONError, NonCanonicalNumberError)
            detail = f"{rule}: {exc}" if rule else "not valid JSON"
            return PackageVerdict(
                None, False, (f"manifest.json: {detail}",),
                ("<trace unavailable: manifest is not valid JSON>",))

        damaged: list[str] = []
        files = manifest.get("files") if isinstance(manifest, dict) else None
        declared_names: set[str] = set()
        if not isinstance(files, list):
            damaged.append("manifest.json: no file list")
        else:
            for entry in files:
                name = entry.get("name") if isinstance(entry, dict) else None
                expected = entry.get("sha256") if isinstance(entry, dict) else None
                if not isinstance(name, str) or not isinstance(expected, str):
                    damaged.append(f"<malformed manifest entry>: {entry!r}")
                    continue
                if name in declared_names:
                    damaged.append(f"<duplicate manifest entry>: {name}")
                    continue
                declared_names.add(name)
                if name not in names:
                    damaged.append(name)
                    continue
                try:
                    if name in _FIXED_JSON_MEMBERS:
                        payload = _read_member(
                            archive, name, limit=_FIXED_MEMBER_MAX_BYTES)
                        actual = _SHA256_PREFIX + hashlib.sha256(payload).hexdigest()
                    else:
                        # Attachments and proofs intentionally retain arbitrary
                        # size; hash them incrementally rather than buffering.
                        actual = _digest_member(archive, name)
                except _MemberReadError as exc:
                    damaged.append(
                        f"{exc.name}: damaged/refused ({type(exc.cause).__name__})")
                    continue
                if actual != expected:
                    damaged.append(name)
            for name in names:
                if name != _MANIFEST_MEMBER and name not in declared_names:
                    damaged.append(name)

        def read_json(name: str) -> tuple[bool, Any, str | None]:
            if name not in names:
                return False, None, None
            try:
                value = _loads_strict(
                    _read_member(
                        archive, name, limit=_FIXED_MEMBER_MAX_BYTES
                    ).decode("utf-8"))
                return True, value, None
            except _MemberReadError as exc:
                return True, None, (
                    f"{exc.name}: damaged/refused ({type(exc.cause).__name__})")
            except RecursionError:
                return True, None, (
                    f"{name}: nesting beyond what this verifier can parse")
            except (ValueError, UnicodeDecodeError) as exc:
                rule = _strict_parse_error(
                    exc, StrictJSONError, NonCanonicalNumberError)
                if rule in ("J1", "J2"):
                    detail = f"{rule} {name}: {exc}"
                else:
                    detail = f"{name}: {rule}: {exc}"
                return True, None, detail

        trace_present, trace, trace_error = read_json("core/trace.json")
        trace_violations: list[str] = []
        if trace_error:
            trace_violations.append(trace_error)
        elif not trace_present:
            trace_violations.append("TRACE core/trace.json: missing")
        elif not isinstance(trace, dict):
            # Keep a valid-but-non-object JSON value distinct: it violates the
            # package boundary's required trace shape before contract
            # validation can run, rather than becoming a generic validator
            # exception that hides the precise refusal reason.
            trace_violations.append("TRACE core/trace.json: not valid JSON object")
        else:
            spec_present, spec, spec_error = read_json("core/graphspec.json")
            if spec_error:
                trace_violations.append(f"SB0 {spec_error}")
            valid_spec = False
            if not spec_present:
                trace_violations.append(
                    "SB0 core/graphspec.json: missing; spec-correlated "
                    "trace checks were not run")
            elif not isinstance(spec, dict):
                trace_violations.append(
                    "SB0 core/graphspec.json: not a valid JSON object; "
                    "spec-correlated trace checks were not run")
            else:
                try:
                    spec_findings = validate_graphspec(spec)
                except Exception as exc:
                    spec_findings = ()
                    trace_violations.append(
                        f"SB0 core/graphspec.json: validation failed: "
                        f"{type(exc).__name__}: {exc}")
                if spec_findings:
                    trace_violations.extend(
                        f"{finding.rule} core/graphspec{finding.path}: "
                        f"{finding.message}"
                        for finding in spec_findings
                    )
                    trace_violations.append(
                        "SB0 core/graphspec.json: spec-correlated trace "
                        "checks were not run because the GraphSpec is invalid")
                else:
                    valid_spec = True
            try:
                findings = validate_trace(trace, spec=spec if valid_spec else None)
                trace_violations.extend(
                    f"{finding.rule} {finding.path}: {finding.message}"
                    for finding in findings
                )
            except Exception as exc:
                # The package boundary is hostile-input code: a validator bug
                # or an unexpected shape is a violation, never a verifier crash.
                trace_violations.append(
                    f"TRACE core/trace.json: validation failed: {type(exc).__name__}: {exc}"
                )

        verdict = None
        if _RECEIPT_MEMBER in names:
            receipt_present, receipt, receipt_error = read_json(_RECEIPT_MEMBER)
            if receipt_error:
                trace_violations.append(f"RECEIPT {receipt_error}")
            try:
                verdict = verify(
                    receipt if receipt_present and isinstance(receipt, dict) else {},
                    trace if isinstance(trace, dict) else None,
                )
            except Exception as exc:
                # Keep the package boundary total even if a future contract
                # validator gains a field access that an adversarial receipt
                # can break.
                trace_violations.append(
                    f"RECEIPT core/receipt.json: verification failed: "
                    f"{type(exc).__name__}: {exc}"
                )
        return PackageVerdict(verdict, not damaged, tuple(damaged),
                              tuple(trace_violations))
    except (KeyError, ValueError, OSError, zipfile.BadZipFile):
        return PackageVerdict(None, False, ("<malformed package>",),
                              ("<trace unavailable: malformed package>",))
    finally:
        archive.close()
