"""Command-line adapter for the ADR-043 verification/query facade."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from vitruvyan_motus.verification_query import execute_verification_query

_MAX_REQUEST_BYTES = 225 * 1024 * 1024
_SUCCESS = frozenset({"valid", "matched", "completed"})


def _human(result: dict[str, Any]) -> str:
    lines = [f"operation: {result['operation']}", f"outcome: {result['outcome']}", "scope: supplied_inputs", "global_complete: false"]
    subject = result["subject"]
    if subject is not None:
        lines.extend([f"input_id: {subject['input_id']}", f"artifact_kind: {subject['kind']}", f"fingerprint: {subject['fingerprint'] or 'not derived'}"])
    for item in result["violations"]:
        lines.append(f"VIOLATION {item['rule']} {item['path']}: {item['message']}")
    for item in result["findings"]:
        lines.append(f"FINDING {item['status']} {item['path']}: {item['reason']}")
    lines.extend(f"MATCH {item['input_id']} {item['kind']} {item['fingerprint'] or 'not derived'}" for item in result["matches"])
    lines.append(f"records: {len(result['records'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="motus-evidence", description="Run one ADR-043 verification/query request over exact supplied Motus artifacts.")
    parser.add_argument("request", help="strict UTF-8 JSON request file")
    parser.add_argument("--json", action="store_true", dest="json_output", help="emit the stable machine-readable result envelope")
    args = parser.parse_args(argv)
    path = Path(args.request)
    try:
        if path.stat().st_size > _MAX_REQUEST_BYTES:
            raise ValueError("request exceeds the 225 MiB CLI input limit")
        raw = path.read_bytes().decode("utf-8")
        validate = importlib.import_module("vitruvyan_motus.contract.validate")
        request = validate._loads_strict(raw, governed_as="verification-query")
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        print(f"error: cannot load request: {exc}", file=sys.stderr)
        return 2
    try:
        result = execute_verification_query(request)
    except (TypeError, ValueError) as exc:
        print(f"error: cannot execute request: {exc}", file=sys.stderr)
        return 2
    if args.json_output:
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    else:
        print(_human(result))
    if result["outcome"] == "invalid_request":
        return 2
    return 0 if result["outcome"] in _SUCCESS else 1


if __name__ == "__main__":
    raise SystemExit(main())
