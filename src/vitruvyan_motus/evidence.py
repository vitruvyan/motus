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
import json
import zipfile
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
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (ValueError, UnicodeDecodeError, KeyError, zipfile.BadZipFile):
            return PackageVerdict(None, False, ("manifest.json: not valid JSON",),
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
                declared_names.add(name)
                if name not in names:
                    damaged.append(name)
                    continue
                actual = "sha256:" + hashlib.sha256(archive.read(name)).hexdigest()
                if actual != expected:
                    damaged.append(name)
            for name in names:
                if name != "manifest.json" and name not in declared_names:
                    damaged.append(name)

        def read_json(name: str) -> tuple[bool, Any]:
            if name not in names:
                return False, None
            try:
                return True, json.loads(archive.read(name).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, KeyError, zipfile.BadZipFile):
                return True, None

        # These imports are deliberately local: bare import of the kernel must
        # not pull jsonschema into the process.
        from vitruvyan_motus.contract.validate import validate_graphspec, validate_trace, verify

        trace_present, trace = read_json("core/trace.json")
        trace_violations: list[str] = []
        if not trace_present:
            trace_violations.append("TRACE core/trace.json: missing")
        elif not isinstance(trace, dict):
            trace_violations.append("TRACE core/trace.json: not valid JSON object")
        else:
            spec_present, spec = read_json("core/graphspec.json")
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
            receipt_present, receipt = read_json("core/receipt.json")
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
