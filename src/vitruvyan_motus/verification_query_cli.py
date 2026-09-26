"""Command-line adapter for the ADR-043 verification/query facade."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any

from vitruvyan_motus.verification_query import execute_verification_query

_MAX_REQUEST_BYTES = 225 * 1024 * 1024
_SUCCESS = frozenset({"valid", "matched", "completed"})


def _terminal_safe(value: Any) -> str:
    text = str(value)
    return "".join(
        character
        if unicodedata.category(character) not in {"Cc", "Cf", "Cs"}
        else f"\\u{ord(character):04x}"
        for character in text
    )


def _human(result: dict[str, Any]) -> str:
    safe = _terminal_safe
    lines = [f"operation: {safe(result['operation'])}", f"outcome: {safe(result['outcome'])}", "scope: supplied_inputs", "global_complete: false"]
    subject = result["subject"]
    if subject is not None:
        lines.extend([f"input_id: {safe(subject['input_id'])}", f"artifact_kind: {safe(subject['kind'])}", f"fingerprint: {safe(subject['fingerprint'] or 'not derived')}"])
    for item in result["violations"]:
        lines.append(f"VIOLATION {safe(item['rule'])} {safe(item['path'])}: {safe(item['message'])}")
    for item in result["findings"]:
        lines.append(f"FINDING {safe(item['status'])} {safe(item['path'])}: {safe(item['reason'])}")
    lines.extend(f"MATCH {safe(item['input_id'])} {safe(item['kind'])} {safe(item['fingerprint'] or 'not derived')}" for item in result["matches"])
    lines.append(f"records: {len(result['records'])}")
    return "\n".join(lines)


def _read_request_bytes(path: Path, limit: int = _MAX_REQUEST_BYTES) -> bytes:
    """Open once, refuse special streams, and bound allocation during read."""
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("request must be a regular file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("request exceeds the 225 MiB CLI input limit")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="motus-evidence", description="Run one ADR-043 verification/query request over exact supplied Motus artifacts.")
    parser.add_argument("request", help="strict UTF-8 JSON request file")
    parser.add_argument("--json", action="store_true", dest="json_output", help="emit the stable machine-readable result envelope")
    args = parser.parse_args(argv)
    path = Path(args.request)
    try:
        raw = _read_request_bytes(path).decode("utf-8")
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
