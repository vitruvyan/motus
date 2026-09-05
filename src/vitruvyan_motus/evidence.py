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

if TYPE_CHECKING:
    from vitruvyan_motus.commitlog import CommitmentLog
    from vitruvyan_motus.commitments import AnchorReceipt
    from vitruvyan_motus.replay import TraceBundle
    from vitruvyan_motus.contract.validate import Verdict

__all__ = ["pack", "verify_package", "PackageVerdict"]

_PACKAGE_VERSION = "1.0"
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
    "manifest.json", "core/trace.json", "core/graphspec.json", "core/receipt.json",
})


@dataclass(frozen=True, slots=True)
class PackageVerdict:
    """Independent transport, trace, and receipt results for a package."""
    verdict: "Verdict | None"
    transport_ok: bool
    damaged: tuple[str, ...]
    trace_violations: tuple[str, ...] = ()


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
    return "sha256:" + digest.hexdigest()


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
    anchors: Iterable[AnchorReceipt] = (),
    proofs: Mapping[str, bytes] = {},
    attachments: Mapping[str, bytes] = {},
) -> bytes:
    """Pack a bundle into the fixed evidence layout.

    A commitment log must have sealed the execution window first. When no log
    is supplied the package honestly contains no receipt.
    """
    from vitruvyan_motus.trace import _canonical_bytes
    trace_dict = bundle.trace.to_dict()
    graph_dict = bundle.spec.to_dict()
    if log is not None:
        from vitruvyan_motus.commitlog import CommitmentLog  # type: ignore[import-not-found]
        run_id = trace_dict["run"]["run_id"]
        execution_ref = log.find_execution_ref(run_id)
        receipt = log.receipt_for(execution_ref, anchors=anchors)
        execution = dict(receipt["execution"])
    else:
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
        members.append(("core/receipt.json", _canonical_bytes(receipt)))
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
        files.append({"name": name, "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
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
    members.append(("manifest.json", _canonical_bytes(manifest)))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(members):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 0
            archive.writestr(info, payload)
    return buffer.getvalue()


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
        seen_names: set[str] = set()
        duplicate_reported: set[str] = set()
        duplicate_names: list[str] = []
        for name in names:
            if name in seen_names and name not in duplicate_reported:
                duplicate_names.append(name)
                duplicate_reported.add(name)
            seen_names.add(name)
        if duplicate_names:
            return PackageVerdict(
                None, False,
                tuple(f"<duplicate member>: {name}" for name in duplicate_names),
                ("<trace unavailable: duplicate physical member name>",),
            )
        unsafe = [name for name in names if not _is_safe_member_name(name)]
        if unsafe:
            return PackageVerdict(
                None, False,
                tuple(f"<unsafe member name>: {name}" for name in unsafe),
                ("<trace unavailable: unsafe member name>",),
            )
        if "manifest.json" not in names:
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
                    archive, "manifest.json", limit=_FIXED_MEMBER_MAX_BYTES
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
                        actual = "sha256:" + hashlib.sha256(payload).hexdigest()
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
                if name != "manifest.json" and name not in declared_names:
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
        if "core/receipt.json" in names:
            receipt_present, receipt, receipt_error = read_json("core/receipt.json")
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
