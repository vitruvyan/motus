"""ADR-040 supplied lineage and scope bindings for immutable retention claims.

These checks establish relationships among the documents supplied by a caller.
They do not establish a complete record universe or physical custody.
"""
from __future__ import annotations

import importlib
import heapq
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Literal

if TYPE_CHECKING:
    from vitruvyan_motus.contract.validate import Violation

Status = Literal["matched", "mismatched", "missing", "not_verified", "conflict"]

_STABLE_ID = {
    "retention-policy-declaration": "policy_id",
    "legal-hold-declaration": "hold_id",
    "retention-scope-snapshot": "snapshot_id",
    "retention-trigger-occurrence": "occurrence_id",
    "retention-application": "application_id",
    "custody-observation": "observation_id",
}
_DECLARATION_SOURCE_KIND = {
    "retention-policy-declaration": "retention_policy_declaration",
    "legal-hold-declaration": "legal_hold_declaration",
}


@dataclass(frozen=True, slots=True)
class RetentionFinding:
    path: str
    status: Status
    expected: str | None
    observed: str | tuple[str, ...] | None
    reason: str


@dataclass(frozen=True, slots=True)
class RetentionLineageVerdict:
    kind: str
    ordered_fingerprints: tuple[str, ...]
    violations: tuple[tuple[int, tuple["Violation", ...]], ...]
    findings: tuple[RetentionFinding, ...]


@dataclass(frozen=True, slots=True)
class RetentionArtifactIdentity:
    kind: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class RetentionScopeVerdict:
    declaration_fingerprint: str | None
    snapshot_fingerprint: str | None
    artifacts: tuple[RetentionArtifactIdentity, ...]
    declaration_violations: tuple["Violation", ...]
    snapshot_violations: tuple["Violation", ...]
    findings: tuple[RetentionFinding, ...]


def _contract_validate():
    return importlib.import_module("vitruvyan_motus.contract.validate")


def _snapshot(validate: Any, document: dict[str, Any]) -> dict[str, Any]:
    return json.loads(validate.canonical_json(document).decode("utf-8"))


def _validator(validate: Any, kind: str):
    return getattr(validate, "validate_" + kind.replace("-", "_"))


def _fingerprint(validate: Any, kind: str, document: dict[str, Any]) -> str:
    return getattr(validate, kind.replace("-", "_") + "_fingerprint")(document)


def verify_retention_lineage(
    kind: str, documents: Iterable[dict[str, Any]],
) -> RetentionLineageVerdict:
    """Order every supplied revision by its stated immediate predecessor.

    An absent predecessor is unresolved in this supplied view. Duplicate
    fingerprints are ambiguous even if a hash function were to collide. No
    timestamp or transport position can resolve an amendment fork.
    """
    if kind not in _STABLE_ID:
        raise ValueError(f"unsupported retention kind: {kind!r}")
    if isinstance(documents, (str, bytes, dict)):
        raise TypeError("documents must be an iterable of dicts")
    validate = _contract_validate()
    validator = _validator(validate, kind)
    rows: list[tuple[str, bytes, dict[str, Any]]] = []
    violations: list[tuple[int, tuple[Violation, ...]]] = []
    findings: list[RetentionFinding] = []
    for index, value in enumerate(documents):
        if not isinstance(value, dict):
            raise TypeError(f"documents[{index}] must be a dict")
        issues = tuple(validator(value))
        if issues:
            violations.append((index, issues))
            findings.append(RetentionFinding(
                f"$.documents[{index}]", "not_verified", None, None,
                "the supplied document violates its structural contract; "
                "no lineage identity was derived",
            ))
            continue
        document = _snapshot(validate, value)
        rows.append((_fingerprint(validate, kind, document),
                     validate.canonical_json(document), document))

    # A fingerprint may not be used as an unambiguous predecessor locator when
    # two supplied rows carry it, including a deliberately induced collision.
    by_fingerprint: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        by_fingerprint.setdefault(row[0], []).append(index)
    for fingerprint, indexes in sorted(by_fingerprint.items()):
        if len(indexes) > 1:
            findings.append(RetentionFinding(
                f"lineage:{fingerprint}", "conflict", fingerprint,
                tuple(fingerprint for _ in indexes),
                "multiple supplied documents have this exact fingerprint; "
                "predecessor identity is ambiguous",
            ))

    children: dict[int, list[int]] = {index: [] for index in range(len(rows))}
    successors: dict[str, list[str]] = {}
    stable_field = _STABLE_ID[kind]
    origins: dict[tuple[str, str], list[str]] = {}
    for index, (fingerprint, _, document) in enumerate(rows):
        predecessor = document.get("supersedes")
        if predecessor is None:
            origins.setdefault((document["producer_namespace"], document[stable_field]),
                               []).append(fingerprint)
            continue
        path = f"lineage:{fingerprint}.supersedes"
        if predecessor == fingerprint:
            findings.append(RetentionFinding(
                path, "conflict", predecessor, fingerprint,
                "a revision cannot supersede its own exact fingerprint",
            ))
            continue
        candidates = by_fingerprint.get(predecessor, ())
        if not candidates:
            findings.append(RetentionFinding(
                path, "not_verified", predecessor, None,
                "the immediate predecessor is absent from this supplied view",
            ))
            continue
        if len(candidates) != 1:
            findings.append(RetentionFinding(
                path, "conflict", predecessor,
                tuple(rows[item][0] for item in candidates),
                "the predecessor fingerprint identifies multiple supplied rows",
            ))
            continue
        parent = candidates[0]
        prior = rows[parent][2]
        if (prior["producer_namespace"], prior[stable_field]) != (
            document["producer_namespace"], document[stable_field]
        ):
            findings.append(RetentionFinding(
                path, "mismatched", predecessor, rows[parent][0],
                "a predecessor must retain its kind, producer namespace "
                "and stable identifier",
            ))
            continue
        children[parent].append(index)
        successors.setdefault(predecessor, []).append(fingerprint)
        findings.append(RetentionFinding(
            path, "matched", predecessor, rows[parent][0],
            "the supplied predecessor has the same kind, namespace and stable identifier",
        ))
    for predecessor, descendants in sorted(successors.items()):
        if len(descendants) > 1:
            findings.append(RetentionFinding(
                f"lineage:{predecessor}", "conflict", predecessor,
                tuple(sorted(descendants)),
                "more than one revision claims this immediate predecessor; "
                "all amendment branches remain visible",
            ))
    for (namespace, stable_id), fingerprints in sorted(origins.items()):
        if len(fingerprints) > 1:
            findings.append(RetentionFinding(
                f"lineage:origin:{namespace}:{stable_id}", "conflict", None,
                tuple(sorted(fingerprints)),
                "more than one supplied revision claims the same lineage origin; "
                "all origins remain visible",
            ))

    def row_key(index: int) -> tuple[str, bytes]:
        return rows[index][0], rows[index][1]

    # Iterative Kosaraju separates cycle members from descendants blocked by a
    # cycle. Condensation then restores parent-before-child order everywhere it
    # is possible, without recursion depth depending on the supplied chain.
    visited: set[int] = set()
    finishing: list[int] = []
    for root in sorted(range(len(rows)), key=row_key):
        stack = [(root, False)]
        while stack:
            node, exiting = stack.pop()
            if exiting:
                finishing.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend((child, False) for child in
                             sorted(children[node], key=row_key, reverse=True))
    reverse_edges: dict[int, list[int]] = {i: [] for i in range(len(rows))}
    for parent, descendants in children.items():
        for descendant in descendants:
            reverse_edges[descendant].append(parent)
    component_of: dict[int, int] = {}
    components: list[tuple[int, ...]] = []
    for root in reversed(finishing):
        if root in component_of:
            continue
        component_id = len(components)
        members: list[int] = []
        stack = [root]
        component_of[root] = component_id
        while stack:
            node = stack.pop()
            members.append(node)
            for parent in reverse_edges[node]:
                if parent not in component_of:
                    component_of[parent] = component_id
                    stack.append(parent)
        components.append(tuple(sorted(members, key=row_key)))

    component_key = tuple(row_key(members[0]) for members in components)
    component_children: dict[int, set[int]] = {
        i: set() for i in range(len(components))
    }
    component_indegree = [0] * len(components)
    for parent, descendants in children.items():
        source_component = component_of[parent]
        for descendant in descendants:
            target_component = component_of[descendant]
            if (source_component != target_component and
                    target_component not in component_children[source_component]):
                component_children[source_component].add(target_component)
                component_indegree[target_component] += 1
    ready = [(component_key[i], i) for i, degree in
             enumerate(component_indegree) if degree == 0]
    heapq.heapify(ready)
    ordered: list[int] = []
    while ready:
        _, component_id = heapq.heappop(ready)
        members = components[component_id]
        if len(members) > 1:
            findings.append(RetentionFinding(
                "lineage:cycle", "conflict", None,
                tuple(rows[i][0] for i in members),
                "these exact revisions form a predecessor cycle; no revision is selected",
            ))
        ordered.extend(members)
        for descendant in component_children[component_id]:
            component_indegree[descendant] -= 1
            if component_indegree[descendant] == 0:
                heapq.heappush(ready, (component_key[descendant], descendant))
    findings.sort(key=lambda item: (item.path, item.status, item.expected or "",
                                    str(item.observed), item.reason))
    return RetentionLineageVerdict(
        kind, tuple(rows[i][0] for i in ordered), tuple(violations), tuple(findings),
    )


def resolve_supplied_retention_scope(
    declaration: dict[str, Any], *, snapshot: dict[str, Any] | None = None,
) -> RetentionScopeVerdict:
    """Bind one declaration selector to only the exact supplied identities.

    A matched snapshot finding establishes its producer's source binding, not
    that its enumerated artifacts are complete or remain in custody.
    """
    if not isinstance(declaration, dict):
        raise TypeError("declaration must be a dict")
    if snapshot is not None and not isinstance(snapshot, dict):
        raise TypeError("snapshot must be a dict or None")
    validate = _contract_validate()
    snapshot_issues = (tuple(validate.validate_retention_scope_snapshot(snapshot))
                       if snapshot is not None else ())
    supplied = (_snapshot(validate, snapshot)
                if snapshot is not None and not snapshot_issues else None)
    snapshot_fingerprint = (validate.retention_scope_snapshot_fingerprint(supplied)
                            if supplied is not None else None)
    kind = "legal-hold-declaration" if "hold_id" in declaration else "retention-policy-declaration"
    issues = tuple(_validator(validate, kind)(declaration))
    if issues:
        return RetentionScopeVerdict(None, snapshot_fingerprint, (), issues,
                                     snapshot_issues, (
            RetentionFinding("$.declaration", "not_verified", None, None,
                             "the declaration violates its structural contract"),
        ))
    source = _snapshot(validate, declaration)
    fingerprint = _fingerprint(validate, kind, source)
    scope = source.get("scope")
    if scope is None:
        return RetentionScopeVerdict(fingerprint, snapshot_fingerprint, (), (),
                                     snapshot_issues, (
            RetentionFinding("$.scope", "not_verified", None, None,
                             "this declaration revision states no scope"),
        ))
    if scope["kind"] == "exact_artifacts":
        artifacts = tuple(RetentionArtifactIdentity(item["kind"], item["fingerprint"])
                          for item in scope["artifacts"])
        findings = [RetentionFinding(
            "$.scope.artifacts", "matched", fingerprint, fingerprint,
            "exact typed identities are enumerated by the declaration itself",
        )]
        if snapshot is not None:
            findings.append(RetentionFinding(
                "$.snapshot", "mismatched", None,
                snapshot_fingerprint if snapshot_fingerprint is not None else "invalid",
                "a scope snapshot was supplied for a declaration that already "
                "enumerates exact artifacts; it was not used to resolve membership",
            ))
        return RetentionScopeVerdict(fingerprint, snapshot_fingerprint, artifacts,
                                     (), snapshot_issues, tuple(findings))
    if snapshot is None:
        return RetentionScopeVerdict(fingerprint, None, (), (), (), (
            RetentionFinding("$.snapshot", "missing", fingerprint, None,
                             "this selector requires an exact supplied scope snapshot"),
        ))
    if snapshot_issues:
        return RetentionScopeVerdict(fingerprint, None, (), (), snapshot_issues, (
            RetentionFinding("$.snapshot", "not_verified", fingerprint, None,
                             "the supplied scope snapshot violates its structural contract"),
        ))
    expected_kind = _DECLARATION_SOURCE_KIND[kind]
    findings = []
    if supplied["source"]["kind"] != expected_kind:
        findings.append(RetentionFinding(
            "$.snapshot.source.kind", "mismatched", expected_kind,
            supplied["source"]["kind"], "snapshot source kind differs from the declaration",
        ))
    if supplied["source"]["fingerprint"] != fingerprint:
        findings.append(RetentionFinding(
            "$.snapshot.source.fingerprint", "mismatched", fingerprint,
            supplied["source"]["fingerprint"],
            "snapshot source must name this exact declaration revision",
        ))
    if supplied["producer_namespace"] != source["producer_namespace"]:
        findings.append(RetentionFinding(
            "$.snapshot.producer_namespace", "mismatched",
            source["producer_namespace"], supplied["producer_namespace"],
            "snapshot and declaration producer namespaces differ",
        ))
    if findings:
        return RetentionScopeVerdict(fingerprint, snapshot_fingerprint, (), (), (),
                                     tuple(findings))
    artifacts = tuple(RetentionArtifactIdentity(item["kind"], item["fingerprint"])
                      for item in supplied["artifacts"])
    return RetentionScopeVerdict(fingerprint, snapshot_fingerprint, artifacts, (), (), (
        RetentionFinding("$.snapshot.source", "matched", fingerprint, fingerprint,
                         "the exact supplied producer snapshot binds this declaration; "
                         "membership completeness and custody are unverified"),
    ))
