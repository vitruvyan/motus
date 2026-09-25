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

__all__ = (
    "RetentionFinding",
    "RetentionArtifactIdentity",
    "RetentionLineageVerdict",
    "RetentionScopeVerdict",
    "RetentionApplicationBindingVerdict",
    "RetentionBlockerVerdict",
    "verify_retention_lineage",
    "resolve_supplied_retention_scope",
    "verify_retention_application_bindings",
    "evaluate_supplied_retention_blocker",
)

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


BlockerStatus = Literal[
    "blocked_by_supplied_hold", "conflicting_supplied_hold", "missing_binding",
    "not_verified", "no_blocker_in_supplied_evidence",
]


@dataclass(frozen=True, slots=True)
class RetentionApplicationBindingVerdict:
    application_fingerprint: str | None
    application_violations: tuple["Violation", ...]
    supplied_violations: tuple[tuple[str, int, tuple["Violation", ...]], ...]
    findings: tuple[RetentionFinding, ...]


@dataclass(frozen=True, slots=True)
class RetentionBlockerVerdict:
    """A verdict over only the hold declarations and snapshots supplied here."""

    status: BlockerStatus
    artifact: RetentionArtifactIdentity | None
    violations: tuple[tuple[str, int, tuple["Violation", ...]], ...]
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


def _supplied_pool(validate, kind: str, values: Iterable[dict[str, Any]]):
    if isinstance(values, (str, bytes, dict)):
        raise TypeError(f"{kind} documents must be an iterable of dicts")
    valid: list[tuple[str, dict[str, Any]]] = []
    invalid: list[tuple[str, int, tuple[Violation, ...]]] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise TypeError(f"{kind} documents[{index}] must be a dict")
        issues = tuple(_validator(validate, kind)(value))
        if issues:
            invalid.append((kind, index, issues))
        else:
            document = _snapshot(validate, value)
            valid.append((_fingerprint(validate, kind, document), document))
    valid.sort(key=lambda pair: (pair[0], validate.canonical_json(pair[1])))
    return tuple(valid), tuple(invalid)


def _exact_reference_finding(
    path: str, expected: str, pool: tuple[tuple[str, dict[str, Any]], ...],
    invalid: bool,
) -> RetentionFinding:
    observed = tuple(sorted({fingerprint for fingerprint, _ in pool}))
    if expected in observed:
        return RetentionFinding(path, "matched", expected, expected,
                                "the exact supplied document fingerprint matches")
    if observed:
        return RetentionFinding(path, "mismatched", expected, observed,
                                "supplied valid documents have different exact fingerprints")
    if invalid:
        return RetentionFinding(path, "not_verified", expected, None,
                                "supplied documents violate their structural contract")
    return RetentionFinding(path, "missing", expected, None,
                            "the exact referenced document was not supplied")


def verify_retention_application_bindings(
    application: dict[str, Any], *, policy: dict[str, Any] | None = None,
    holds: Iterable[dict[str, Any]] = (),
    snapshots: Iterable[dict[str, Any]] = (),
) -> RetentionApplicationBindingVerdict:
    """Verify exact referenced revisions on one claimed custodian action.

    The operation and outcome remain claims. Omitted holds are not evidence of
    their global absence, and this function neither verifies legal authority
    nor infers custody from an application record.
    """
    if not isinstance(application, dict):
        raise TypeError("application must be a dict")
    if policy is not None and not isinstance(policy, dict):
        raise TypeError("policy must be a dict or None")
    validate = _contract_validate()
    app_issues = tuple(validate.validate_retention_application(application))
    if app_issues:
        return RetentionApplicationBindingVerdict(None, app_issues, (), (
            RetentionFinding("$.application", "not_verified", None, None,
                             "the application violates its structural contract"),
        ))
    app = _snapshot(validate, application)
    app_fingerprint = validate.retention_application_fingerprint(app)
    policies, policy_issues = _supplied_pool(
        validate, "retention-policy-declaration", () if policy is None else (policy,),
    )
    hold_docs, hold_issues = _supplied_pool(validate, "legal-hold-declaration", holds)
    scope_docs, scope_issues = _supplied_pool(validate, "retention-scope-snapshot", snapshots)
    supplied_violations = policy_issues + hold_issues + scope_issues
    findings: list[RetentionFinding] = []
    for label, pool in (("policy", policies), ("hold", hold_docs),
                        ("snapshot", scope_docs)):
        counts: dict[str, int] = {}
        for fingerprint, _ in pool:
            counts[fingerprint] = counts.get(fingerprint, 0) + 1
        for fingerprint, count in sorted(counts.items()):
            if count > 1:
                findings.append(RetentionFinding(
                    f"{label}:{fingerprint}", "conflict", fingerprint,
                    tuple(fingerprint for _ in range(count)),
                    "multiple supplied documents have one exact fingerprint; "
                    "the binding source is ambiguous",
                ))
    findings.append(_exact_reference_finding(
        "$.policy_fingerprint", app["policy_fingerprint"], policies,
        bool(policy_issues),
    ))
    cited_holds = set(app.get("hold_fingerprints", ()))
    cited_scopes = set(app.get("scope_snapshot_fingerprints", ()))
    for index, expected in enumerate(app.get("hold_fingerprints", ())):
        findings.append(_exact_reference_finding(
            f"$.hold_fingerprints[{index}]", expected, hold_docs,
            bool(hold_issues),
        ))
    for index, expected in enumerate(app.get("scope_snapshot_fingerprints", ())):
        findings.append(_exact_reference_finding(
            f"$.scope_snapshot_fingerprints[{index}]", expected, scope_docs,
            bool(scope_issues),
        ))
    if "hold_fingerprints" not in app:
        findings.append(RetentionFinding(
            "$.hold_fingerprints", "not_verified", None, None,
            "hold bindings are omitted; this says nothing about other holds",
        ))

    source_documents = {
        ("retention_policy_declaration", fingerprint): document
        for fingerprint, document in policies
        if fingerprint == app["policy_fingerprint"]
    }
    source_documents.update({
        ("legal_hold_declaration", fingerprint): document
        for fingerprint, document in hold_docs if fingerprint in cited_holds
    })
    for fingerprint, scope in scope_docs:
        if fingerprint not in cited_scopes:
            continue
        source = scope["source"]
        source_key = (source["kind"], source["fingerprint"])
        source_document = source_documents.get(source_key)
        path = f"scope:{fingerprint}.source"
        if source_document is None:
            findings.append(RetentionFinding(
                path, "not_verified", source["fingerprint"], None,
                "the snapshot source is not an exact supplied policy or cited hold",
            ))
        elif scope["producer_namespace"] != source_document["producer_namespace"]:
            findings.append(RetentionFinding(
                path, "mismatched", source_document["producer_namespace"],
                scope["producer_namespace"],
                "snapshot and source producer namespaces differ",
            ))
        else:
            findings.append(RetentionFinding(
                path, "matched", source["fingerprint"], source["fingerprint"],
                "the snapshot names an exact supplied source in the same namespace",
            ))
    findings.sort(key=lambda item: (item.path, item.status, item.expected or "",
                                    str(item.observed), item.reason))
    return RetentionApplicationBindingVerdict(
        app_fingerprint, (), supplied_violations, tuple(findings),
    )


def _artifact_violations(validate, artifact: Any) -> tuple[Violation, ...]:
    root = validate.load_retention_policy_declaration_schema()
    wrapper = {
        "$schema": root["$schema"],
        "$ref": root["$id"] + "#/$defs/ArtifactIdentity",
    }
    validator = validate._retention_validator("retention-artifact-identity", wrapper)
    violations, structural = validate._j1_violations(artifact)
    if structural:
        return tuple(violations)
    return tuple(violations + validate._schema_violations(wrapper, validator, artifact))


def evaluate_supplied_retention_blocker(
    artifact: dict[str, Any], *, holds: Iterable[dict[str, Any]] = (),
    snapshots: Iterable[dict[str, Any]] = (),
) -> RetentionBlockerVerdict:
    """Evaluate one artifact against only caller-supplied producer hold claims.

    A blocked result means a supplied placed or amended declaration enumerates
    this exact identity. It does not validate legal authority, enforcement or
    the completeness of the caller's hold universe. A release/cancellation is
    never interpreted as authority to disregard an earlier placement.
    """
    if not isinstance(artifact, dict):
        raise TypeError("artifact must be a dict")
    validate = _contract_validate()
    artifact_issues = _artifact_violations(validate, artifact)
    if artifact_issues:
        return RetentionBlockerVerdict("not_verified", None,
                                      (("artifact", 0, artifact_issues),), (
            RetentionFinding("$.artifact", "not_verified", None, None,
                             "the artifact identity violates its contract"),
        ))
    identity = RetentionArtifactIdentity(artifact["kind"], artifact["fingerprint"])
    if isinstance(holds, (str, bytes, dict)):
        raise TypeError("legal-hold-declaration documents must be an iterable of dicts")
    hold_values = tuple(holds)
    hold_docs, hold_issues = _supplied_pool(
        validate, "legal-hold-declaration", hold_values,
    )
    if isinstance(snapshots, (str, bytes, dict)):
        raise TypeError("retention-scope-snapshot documents must be an iterable of dicts")
    snapshot_values = tuple(snapshots)
    scope_docs, scope_issues = _supplied_pool(
        validate, "retention-scope-snapshot", snapshot_values,
    )
    invalid_snapshot_sources: set[str] = set()
    for _, index, _ in scope_issues:
        source = snapshot_values[index].get("source")
        if (isinstance(source, dict) and
                source.get("kind") == "legal_hold_declaration" and
                isinstance(source.get("fingerprint"), str)):
            # Only a readable exact source may associate an invalid document
            # with a hold; the invalid snapshot itself never resolves scope.
            invalid_snapshot_sources.add(source["fingerprint"])
    violations = hold_issues + scope_issues
    findings: list[RetentionFinding] = []
    if violations:
        findings.append(RetentionFinding(
            "$.supplied", "not_verified", None, None,
            "one or more supplied hold or snapshot documents violate their contract",
        ))
    hold_lineage = verify_retention_lineage(
        "legal-hold-declaration", (document for _, document in hold_docs),
    )
    findings.extend(hold_lineage.findings)
    chains: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = {}
    for fingerprint, hold in hold_docs:
        chains.setdefault((hold["producer_namespace"], hold["hold_id"]), []).append(
            (fingerprint, hold)
        )
    invalid_chains: set[tuple[str, str]] = set()
    for _, index, _ in hold_issues:
        value = hold_values[index]
        if isinstance(value, dict):
            namespace, hold_id = value.get("producer_namespace"), value.get("hold_id")
            if isinstance(namespace, str) and isinstance(hold_id, str):
                invalid_chains.add((namespace, hold_id))
    states: list[dict[str, bool]] = []
    for chain_key, records in sorted(chains.items()):
        lineage = verify_retention_lineage(
            "legal-hold-declaration", (document for _, document in records),
        )
        state = {
            "matched": False,
            "conflict": any(item.status == "conflict" for item in lineage.findings),
            "missing": any(item.status == "not_verified" and
                           item.path.endswith(".supersedes")
                           for item in lineage.findings),
            "unverified": chain_key in invalid_chains or any(
                item.status == "mismatched" for item in lineage.findings
            ),
            "terminal": False,
        }
        for hold_fingerprint, hold in records:
            if hold["action"] in ("released", "cancelled"):
                state["terminal"] = True
                findings.append(RetentionFinding(
                    f"hold:{hold_fingerprint}.action", "not_verified", None,
                    hold["action"],
                    "release or cancellation is a producer claim; authority to "
                    "disregard prior placement is not established",
                ))
                continue
            scope = hold["scope"]
            invalid_bound_snapshot = (scope["kind"] != "exact_artifacts" and
                                      hold_fingerprint in invalid_snapshot_sources)
            if invalid_bound_snapshot:
                state["unverified"] = True
                findings.append(RetentionFinding(
                    f"hold:{hold_fingerprint}.snapshots", "not_verified",
                    hold_fingerprint, None,
                    "a supplied snapshot naming this exact selector hold revision "
                    "violates its structural contract; scope is not verified",
                ))
            bound_snapshots = tuple((fingerprint, document)
                                    for fingerprint, document in scope_docs
                                    if document["source"] == {
                                        "kind": "legal_hold_declaration",
                                        "fingerprint": hold_fingerprint,
                                    })
            if scope["kind"] != "exact_artifacts" and len(bound_snapshots) > 1:
                state["conflict"] = True
                findings.append(RetentionFinding(
                    f"hold:{hold_fingerprint}.snapshots", "conflict", hold_fingerprint,
                    tuple(fingerprint for fingerprint, _ in bound_snapshots),
                    "multiple supplied snapshots bind this selector revision; "
                    "none is selected or unioned",
                ))
                continue
            # An exact list already establishes direct membership. A supplied
            # snapshot is still inspected and reported, but cannot replace it.
            candidates = (bound_snapshots if scope["kind"] == "exact_artifacts"
                          and bound_snapshots else ((None, None),))
            if scope["kind"] != "exact_artifacts":
                candidates = (bound_snapshots[0],) if bound_snapshots else ((None, None),)
            if invalid_bound_snapshot and not bound_snapshots:
                continue
            for _, supplied_snapshot in candidates:
                result = resolve_supplied_retention_scope(
                    hold, snapshot=supplied_snapshot,
                )
                findings.extend(RetentionFinding(
                    f"hold:{hold_fingerprint}.scope:{item.path}", item.status,
                    item.expected, item.observed, item.reason,
                ) for item in result.findings)
                if scope["kind"] != "exact_artifacts":
                    if any(item.status == "missing" for item in result.findings):
                        state["missing"] = True
                        findings.append(RetentionFinding(
                            f"hold:{hold_fingerprint}.snapshot", "missing",
                            hold_fingerprint, None,
                            "this selector has no exact supplied scope snapshot",
                        ))
                        continue
                    if (result.declaration_violations or result.snapshot_violations or
                            any(item.status in ("mismatched", "not_verified", "conflict")
                                for item in result.findings)):
                        state["unverified"] = True
                        continue
                if identity in result.artifacts:
                    state["matched"] = True
                    findings.append(RetentionFinding(
                        f"hold:{hold_fingerprint}.artifact", "matched",
                        identity.fingerprint, hold_fingerprint,
                        "this supplied producer hold declaration scopes the exact typed artifact",
                    ))
        states.append(state)

    relevant = [state for state in states if state["matched"]]
    complete_blocker = any(
        state["matched"] and not any(state[key] for key in
                                     ("conflict", "terminal", "missing", "unverified"))
        for state in states
    )
    deciding = relevant if relevant else states
    if complete_blocker:
        status: BlockerStatus = "blocked_by_supplied_hold"
    elif any(state["conflict"] for state in deciding):
        status = "conflicting_supplied_hold"
    elif any(state["terminal"] for state in deciding):
        status = "not_verified"
    elif any(state["unverified"] for state in deciding):
        status = "not_verified"
    elif any(state["missing"] for state in deciding):
        status = "missing_binding"
    elif not relevant and violations:
        status = "not_verified"
    elif relevant:
        status = "blocked_by_supplied_hold"
    else:
        status = "no_blocker_in_supplied_evidence"
    findings.sort(key=lambda item: (item.path, item.status, item.expected or "",
                                    str(item.observed), item.reason))
    return RetentionBlockerVerdict(status, identity, violations, tuple(findings))
