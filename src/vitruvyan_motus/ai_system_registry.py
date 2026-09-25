"""ADR-041 verification over caller-supplied AI System Registry records.

Every result is scoped to the exact supplied documents.  Nothing here proves
legal status, authority, deployment, completeness or a globally current state.
"""
from __future__ import annotations

import heapq
import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Literal

if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Violation

__all__ = (
    "AISystemRegistryFinding", "AISystemRegistryLineageVerdict",
    "AISystemRegistrationBindingVerdict", "AISystemLifecycleProjection",
    "AISystemRegistrySnapshotVerdict", "verify_ai_system_registry_lineage",
    "verify_ai_system_registration_binding", "project_supplied_ai_system_lifecycle",
    "verify_ai_system_registry_snapshot",
)

Status = Literal["matched", "mismatched", "missing", "not_verified", "conflict"]
_KINDS = {
    "ai-system-registration": "registration_id",
    "ai-system-registry-event": "event_id",
    "ai-system-registry-snapshot": "snapshot_id",
}


@dataclass(frozen=True, slots=True)
class AISystemRegistryFinding:
    path: str
    status: Status
    expected: str | None
    observed: str | tuple[str, ...] | None
    reason: str


@dataclass(frozen=True, slots=True)
class AISystemRegistryLineageVerdict:
    kind: str
    ordered_fingerprints: tuple[str, ...]
    violations: tuple[tuple[int, tuple["Violation", ...]], ...]
    findings: tuple[AISystemRegistryFinding, ...]


@dataclass(frozen=True, slots=True)
class AISystemRegistrationBindingVerdict:
    registration_fingerprint: str | None
    registration_violations: tuple["Violation", ...]
    manifest_violations: tuple[tuple[int, tuple["Violation", ...]], ...]
    findings: tuple[AISystemRegistryFinding, ...]

    @property
    def binding_complete(self) -> bool:
        return (not self.registration_violations and not self.manifest_violations
                and bool(self.findings)
                and all(item.status == "matched" for item in self.findings))


@dataclass(frozen=True, slots=True)
class AISystemLifecycleProjection:
    registration_id: str | None
    ordered_event_fingerprints: tuple[str, ...]
    terminal_action: str | None
    registration_violations: tuple[tuple[int, tuple["Violation", ...]], ...]
    event_violations: tuple[tuple[int, tuple["Violation", ...]], ...]
    findings: tuple[AISystemRegistryFinding, ...]
    scope: str = "derived only from the supplied records"


@dataclass(frozen=True, slots=True)
class AISystemRegistrySnapshotVerdict:
    snapshot_fingerprint: str | None
    snapshot_violations: tuple["Violation", ...]
    supplied_violations: tuple[tuple[str, int, tuple["Violation", ...]], ...]
    findings: tuple[AISystemRegistryFinding, ...]
    scope: str = "bounded supplied view; not proof of completeness"


def _validate_module():
    return importlib.import_module("vitruvyan_motus.contract.validate")


def _snapshot(validate: Any, document: dict[str, Any]) -> dict[str, Any]:
    return json.loads(validate.canonical_json(document).decode("utf-8"))


def _validator(validate: Any, kind: str):
    return getattr(validate, "validate_" + kind.replace("-", "_"))


def _fingerprint(validate: Any, kind: str, document: dict[str, Any]) -> str:
    return getattr(validate, kind.replace("-", "_") + "_fingerprint")(document)


def _canonicalizable_fingerprint(validate: Any, kind: str, document: dict[str, Any]) -> str | None:
    """Derive identity for schema-invalid JSON without treating it as valid."""
    try:
        validate.canonical_json(document)
        return _fingerprint(validate, kind, document)
    except (TypeError, ValueError):
        return None


def _documents(
    validate: Any, kind: str, values: Iterable[dict[str, Any]],
) -> tuple[list[tuple[int, str, bytes, dict[str, Any]]],
           list[tuple[int, tuple["Violation", ...]]]]:
    if isinstance(values, (str, bytes, dict)):
        raise TypeError("documents must be an iterable of dicts")
    rows = []
    invalid = []
    validator = _validator(validate, kind)
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise TypeError(f"documents[{index}] must be a dict")
        issues = tuple(validator(value))
        if issues:
            invalid.append((index, issues))
            continue
        document = _snapshot(validate, value)
        rows.append((index, _fingerprint(validate, kind, document),
                     validate.canonical_json(document), document))
    return rows, invalid


def verify_ai_system_registry_lineage(
    kind: str, documents: Iterable[dict[str, Any]],
) -> AISystemRegistryLineageVerdict:
    """Order supplied correction revisions without selecting a fork."""
    if kind not in _KINDS:
        raise ValueError(f"unsupported AI System Registry kind: {kind!r}")
    validate = _validate_module()
    rows, invalid = _documents(validate, kind, documents)
    findings = [AISystemRegistryFinding(
        f"$.documents[{index}]", "not_verified", None, None,
        "the supplied document violates its structural contract",
    ) for index, _ in invalid]
    by_fp: dict[str, list[int]] = {}
    for position, (_, fingerprint, _, _) in enumerate(rows):
        by_fp.setdefault(fingerprint, []).append(position)
    for fingerprint, positions in sorted(by_fp.items()):
        if len(positions) > 1:
            findings.append(AISystemRegistryFinding(
                f"lineage:{fingerprint}", "conflict", fingerprint,
                tuple(fingerprint for _ in positions),
                "multiple supplied rows have this exact fingerprint",
            ))

    stable_field = _KINDS[kind]
    edges: dict[int, list[int]] = {i: [] for i in range(len(rows))}
    indegree = [0] * len(rows)
    successors: dict[str, list[str]] = {}
    origins: dict[tuple[str, str, str], list[str]] = {}
    for child, (_, fingerprint, _, document) in enumerate(rows):
        stable = (document["producer_namespace"], document["registry_id"],
                  document[stable_field])
        predecessor = document.get("supersedes")
        if predecessor is None:
            origins.setdefault(stable, []).append(fingerprint)
            continue
        candidates = by_fp.get(predecessor, [])
        path = f"lineage:{fingerprint}.supersedes"
        if predecessor == fingerprint or len(candidates) > 1:
            findings.append(AISystemRegistryFinding(
                path, "conflict", predecessor,
                tuple(rows[i][1] for i in candidates) or fingerprint,
                "the predecessor identity is self-referential or ambiguous",
            ))
        elif not candidates:
            findings.append(AISystemRegistryFinding(
                path, "not_verified", predecessor, None,
                "the immediate predecessor is absent from this supplied view",
            ))
        else:
            parent = candidates[0]
            prior = rows[parent][3]
            prior_stable = (prior["producer_namespace"], prior["registry_id"],
                            prior[stable_field])
            if prior_stable != stable:
                findings.append(AISystemRegistryFinding(
                    path, "mismatched", predecessor, rows[parent][1],
                    "a predecessor must retain kind, namespace, registry and stable id",
                ))
            else:
                edges[parent].append(child)
                indegree[child] += 1
                successors.setdefault(predecessor, []).append(fingerprint)
                findings.append(AISystemRegistryFinding(
                    path, "matched", predecessor, rows[parent][1],
                    "the exact supplied predecessor retains the stable identity",
                ))
    for stable, fingerprints in sorted(origins.items()):
        if len(fingerprints) > 1:
            findings.append(AISystemRegistryFinding(
                "lineage:origin:" + ":".join(stable), "conflict", None,
                tuple(sorted(fingerprints)), "multiple supplied roots claim one stable identity",
            ))
    for predecessor, children in sorted(successors.items()):
        if len(children) > 1:
            findings.append(AISystemRegistryFinding(
                f"lineage:{predecessor}", "conflict", predecessor,
                tuple(sorted(children)), "more than one revision claims this predecessor",
            ))

    def key(i: int) -> tuple[str, bytes]:
        return rows[i][1], rows[i][2]
    ready = [(key(i), i) for i, degree in enumerate(indegree) if degree == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, node = heapq.heappop(ready)
        ordered.append(node)
        for child in sorted(edges[node], key=key):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, (key(child), child))
    remaining = sorted((i for i in range(len(rows)) if i not in set(ordered)), key=key)
    if remaining:
        findings.append(AISystemRegistryFinding(
            "lineage:cycle", "conflict", None,
            tuple(rows[i][1] for i in remaining),
            "the supplied predecessor graph contains a cycle; no winner is selected",
        ))
        ordered.extend(remaining)
    findings.sort(key=lambda item: (item.path, item.status, item.expected or "",
                                    str(item.observed), item.reason))
    return AISystemRegistryLineageVerdict(
        kind, tuple(rows[i][1] for i in ordered), tuple(invalid), tuple(findings)
    )


def verify_ai_system_registration_binding(
    registration: dict[str, Any], *, manifests: Iterable[dict[str, Any]] = (),
) -> AISystemRegistrationBindingVerdict:
    """Check one exact manifest binding; no match implies legal registration."""
    if not isinstance(registration, dict):
        raise TypeError("registration must be a dict")
    validate = _validate_module()
    issues = tuple(validate.validate_ai_system_registration(registration))
    if issues:
        return AISystemRegistrationBindingVerdict(None, issues, (), ())
    document = _snapshot(validate, registration)
    registration_fp = validate.ai_system_registration_fingerprint(document)
    if isinstance(manifests, (str, bytes, dict)):
        raise TypeError("manifests must be an iterable of dicts")
    valid: list[tuple[int, str, dict[str, Any]]] = []
    invalid = []
    invalid_fingerprints: list[str] = []
    for index, value in enumerate(manifests):
        if not isinstance(value, dict):
            raise TypeError(f"manifests[{index}] must be a dict")
        manifest_issues = tuple(validate.validate_system_manifest(value))
        if manifest_issues:
            invalid.append((index, manifest_issues))
            fingerprint = _canonicalizable_fingerprint(
                validate, "system-manifest", value
            )
            if fingerprint is not None:
                invalid_fingerprints.append(fingerprint)
            continue
        snap = _snapshot(validate, value)
        valid.append((index, validate.system_manifest_fingerprint(snap), snap))
    expected = document["system_manifest_fingerprint"]
    matches = [(index, manifest) for index, fp, manifest in valid if fp == expected]
    findings = []
    invalid_match_count = invalid_fingerprints.count(expected)
    if len(matches) + invalid_match_count > 1:
        findings.append(AISystemRegistryFinding(
            "$.system_manifest_fingerprint", "conflict", expected,
            tuple(expected for _ in range(len(matches) + invalid_match_count)),
            "multiple supplied valid or invalid manifests share the cited identity",
        ))
    elif invalid_match_count == 1:
        findings.append(AISystemRegistryFinding(
            "$.system_manifest_fingerprint", "not_verified", expected, expected,
            "the exact supplied System Manifest violates its structural contract",
        ))
    elif not matches:
        findings.append(AISystemRegistryFinding(
            "$.system_manifest_fingerprint", "missing", expected, None,
            "no valid supplied System Manifest has the exact cited fingerprint",
        ))
    else:
        manifest = matches[0][1]
        observed_system = manifest["system"]["id"]
        findings.append(AISystemRegistryFinding(
            "$.system_manifest_fingerprint", "matched", expected, expected,
            "the exact supplied System Manifest matches the cited fingerprint",
        ))
        findings.append(AISystemRegistryFinding(
            "$.system_id", "matched" if observed_system == document["system_id"] else "mismatched",
            document["system_id"], observed_system,
            "the registration system_id is compared with the exact supplied manifest",
        ))
    return AISystemRegistrationBindingVerdict(
        registration_fp, (), tuple(invalid), tuple(findings)
    )


def project_supplied_ai_system_lifecycle(
    registration_id: str, *, registrations: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
) -> AISystemLifecycleProjection:
    """Project one conflict-free event chain from only the supplied records."""
    if not isinstance(registration_id, str):
        raise TypeError("registration_id must be a string")
    validate = _validate_module()
    reg_rows, reg_invalid = _documents(validate, "ai-system-registration", registrations)
    event_rows, event_invalid = _documents(validate, "ai-system-registry-event", events)
    findings = []
    reg_by_fp: dict[str, list[dict[str, Any]]] = {}
    for _, fp, _, doc in reg_rows:
        if doc["registration_id"] == registration_id:
            reg_by_fp.setdefault(fp, []).append(doc)
    candidates = [(fp, doc) for _, fp, _, doc in event_rows
                  if doc["registration_id"] == registration_id]
    scopes = {(doc["producer_namespace"], doc["registry_id"])
              for _, doc in candidates}
    scopes.update((doc["producer_namespace"], doc["registry_id"])
                  for rows in reg_by_fp.values() for doc in rows)
    if len(scopes) > 1:
        findings.append(AISystemRegistryFinding(
            "lifecycle:scope", "conflict", None,
            tuple(sorted(f"{namespace}/{registry}" for namespace, registry in scopes)),
            "the supplied registration_id appears in more than one namespace or registry",
        ))
    by_fp: dict[str, list[dict[str, Any]]] = {}
    for fp, doc in candidates:
        by_fp.setdefault(fp, []).append(doc)
        cited = doc["registration_fingerprint"]
        cited_regs = reg_by_fp.get(cited, [])
        if not cited_regs:
            findings.append(AISystemRegistryFinding(
                f"event:{fp}.registration_fingerprint", "missing", cited, None,
                "the exact registration revision is absent from this supplied view",
            ))
        elif len(cited_regs) > 1:
            findings.append(AISystemRegistryFinding(
                f"event:{fp}.registration_fingerprint", "conflict", cited,
                tuple(cited for _ in cited_regs),
                "multiple supplied registration rows share the cited identity",
            ))
        else:
            cited_reg = cited_regs[0]
            if (cited_reg["producer_namespace"], cited_reg["registry_id"],
                    cited_reg["registration_id"]) != (
                doc["producer_namespace"], doc["registry_id"],
                    doc["registration_id"]):
                findings.append(AISystemRegistryFinding(
                    f"event:{fp}.registration_fingerprint", "mismatched", cited,
                    cited, "the cited registration belongs to another stable registry subject",
                ))
    if any(len(rows) > 1 for rows in by_fp.values()):
        for fp, rows in sorted(by_fp.items()):
            if len(rows) > 1:
                findings.append(AISystemRegistryFinding(
                    f"event:{fp}", "conflict", fp, tuple(fp for _ in rows),
                    "multiple supplied event rows have this exact identity",
                ))
    # Select one explicitly linked correction head for each stable event_id.
    # A fork, missing predecessor or cycle leaves that event unresolved.
    effective: list[tuple[str, dict[str, Any]]] = []
    by_event_id: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for fp, doc in candidates:
        by_event_id.setdefault(doc["event_id"], []).append((fp, doc))
    for event_id, revisions in sorted(by_event_id.items()):
        identities = {fp for fp, _ in revisions}
        parents = []
        bad = False
        for fp, doc in revisions:
            predecessor = doc.get("supersedes")
            if predecessor is not None:
                parents.append(predecessor)
                if predecessor not in identities:
                    findings.append(AISystemRegistryFinding(
                        f"event:{fp}.supersedes", "missing", predecessor, None,
                        "the correction predecessor is absent from this supplied view",
                    ))
                    bad = True
        roots_for_id = [(fp, doc) for fp, doc in revisions if "supersedes" not in doc]
        heads = [(fp, doc) for fp, doc in revisions if fp not in parents]
        if len(roots_for_id) != 1 or len(heads) != 1 or len(set(parents)) != len(parents):
            findings.append(AISystemRegistryFinding(
                f"event-corrections:{event_id}", "conflict", None,
                tuple(sorted(fp for fp, _ in revisions)),
                "event correction lineage must have one root and one unambiguous head",
            ))
            bad = True
        if not bad:
            # A single root and head are insufficient if a detached cycle is
            # also present, so walk every predecessor from the head.
            docs_by_fp = {fp: doc for fp, doc in revisions}
            cursor = heads[0][0]
            visited = set()
            while cursor not in visited:
                visited.add(cursor)
                predecessor = docs_by_fp[cursor].get("supersedes")
                if predecessor is None:
                    break
                cursor = predecessor
            if len(visited) != len(revisions) or cursor in visited and docs_by_fp[cursor].get("supersedes") is not None:
                findings.append(AISystemRegistryFinding(
                    f"event-corrections:{event_id}", "conflict", None,
                    tuple(sorted(fp for fp, _ in revisions)),
                    "event correction lineage is disconnected or cyclic",
                ))
            else:
                effective.append(heads[0])

    roots = [(fp, doc) for fp, doc in effective
             if "predecessor_event_fingerprint" not in doc]
    successors: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    effective_fingerprints = {fp for fp, _ in effective}
    for fp, doc in effective:
        predecessor = doc.get("predecessor_event_fingerprint")
        if predecessor is not None:
            successors.setdefault(predecessor, []).append((fp, doc))
            if predecessor not in effective_fingerprints:
                findings.append(AISystemRegistryFinding(
                    f"event:{fp}.predecessor_event_fingerprint", "missing",
                    predecessor, None, "the lifecycle predecessor is absent from this supplied view",
                ))
    if len(roots) != 1:
        findings.append(AISystemRegistryFinding(
            "lifecycle:root", "conflict" if len(roots) > 1 else "missing", None,
            tuple(sorted(fp for fp, _ in roots)) or None,
            "a projection requires exactly one supplied lifecycle root",
        ))
    ordered = []
    terminal = None
    if len(roots) == 1 and not event_invalid:
        fp, doc = roots[0]
        seen = set()
        while fp not in seen:
            seen.add(fp)
            ordered.append(fp)
            terminal = doc["action"]
            children = successors.get(fp, [])
            if not children:
                break
            if len(children) != 1:
                findings.append(AISystemRegistryFinding(
                    f"lifecycle:{fp}", "conflict", fp,
                    tuple(sorted(item[0] for item in children)),
                    "more than one lifecycle event cites this predecessor",
                ))
                terminal = None
                break
            fp, doc = children[0]
        if fp in seen and successors.get(fp):
            findings.append(AISystemRegistryFinding(
                "lifecycle:cycle", "conflict", None, tuple(ordered),
                "the supplied lifecycle chain contains a cycle",
            ))
            terminal = None
    if (reg_invalid or event_invalid or any(item.status != "matched" for item in findings)
            or len(ordered) != len(effective)):
        terminal = None
    return AISystemLifecycleProjection(
        registration_id, tuple(ordered), terminal, tuple(reg_invalid),
        tuple(event_invalid), tuple(sorted(findings, key=lambda item: item.path)),
    )


def verify_ai_system_registry_snapshot(
    snapshot: dict[str, Any], *, registrations: Iterable[dict[str, Any]] = (),
    events: Iterable[dict[str, Any]] = (),
) -> AISystemRegistrySnapshotVerdict:
    """Verify exact members of one bounded snapshot, never its completeness."""
    if not isinstance(snapshot, dict):
        raise TypeError("snapshot must be a dict")
    validate = _validate_module()
    issues = tuple(validate.validate_ai_system_registry_snapshot(snapshot))
    if issues:
        return AISystemRegistrySnapshotVerdict(None, issues, (), ())
    document = _snapshot(validate, snapshot)
    supplied_violations = []
    findings = []
    indexes: dict[str, Any] = {}
    for kind, values in (("ai-system-registration", registrations),
                         ("ai-system-registry-event", events)):
        supplied = tuple(values)
        rows, invalid = _documents(validate, kind, supplied)
        supplied_violations.extend((kind, index, violations) for index, violations in invalid)
        index: dict[str, list[dict[str, Any]]] = {}
        for _, fp, _, value in rows:
            index.setdefault(fp, []).append(value)
        indexes[kind] = index
        invalid_index: dict[str, int] = {}
        for position, _ in invalid:
            fingerprint = _canonicalizable_fingerprint(validate, kind, supplied[position])
            if fingerprint is not None:
                invalid_index[fingerprint] = invalid_index.get(fingerprint, 0) + 1
        indexes[kind + ":invalid"] = invalid_index
    for field, kind in (("registrations", "ai-system-registration"),
                        ("events", "ai-system-registry-event")):
        for position, expected in enumerate(document[field]):
            matches = indexes[kind].get(expected, [])
            invalid_matches = indexes[kind + ":invalid"].get(expected, 0)
            path = f"$.{field}[{position}]"
            if len(matches) + invalid_matches > 1:
                status, observed, reason = "conflict", tuple(
                    expected for _ in range(len(matches) + invalid_matches)
                ), "multiple valid or invalid supplied rows share the exact identity"
            elif invalid_matches == 1:
                status, observed, reason = "not_verified", expected, "the exact supplied record violates its structural contract"
            elif not matches:
                status, observed, reason = "missing", None, "the exact cited record is absent"
            elif len(matches) > 1:
                status, observed, reason = "conflict", tuple(expected for _ in matches), "the exact identity is ambiguous"
            else:
                candidate = matches[0]
                same_registry = (candidate["producer_namespace"] == document["producer_namespace"]
                                 and candidate["registry_id"] == document["registry_id"])
                status = "matched" if same_registry else "mismatched"
                observed = expected
                reason = "the supplied record matches the snapshot registry scope" if same_registry else "the supplied record belongs to another namespace or registry"
            findings.append(AISystemRegistryFinding(path, status, expected, observed, reason))
    return AISystemRegistrySnapshotVerdict(
        validate.ai_system_registry_snapshot_fingerprint(document), (),
        tuple(supplied_violations), tuple(findings),
    )
