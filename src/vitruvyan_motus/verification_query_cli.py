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

_MAX_REQUEST_MIB = 352
_MAX_REQUEST_BYTES = _MAX_REQUEST_MIB * 1024 * 1024
_SUCCESS = frozenset({"valid", "matched", "completed"})


def _terminal_safe(value: Any) -> str:
    text = str(value)
    return "".join(
        character
        if unicodedata.category(character) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        else f"\\u{ord(character):04x}"
        for character in text
    )


def _human(result: dict[str, Any]) -> str:
    safe = _terminal_safe
    scope = result["scope"]
    lines = [
        f"operation: {safe(result['operation'])}",
        f"outcome: {safe(result['outcome'])}",
        f"scope: {safe(scope['scope_kind'])}",
        f"global_complete: {str(bool(scope['global_complete'])).lower()}",
    ]
    lines.extend(
        f"SCOPE_INPUT {safe(input_id)}" for input_id in scope["input_ids"]
    )
    subject = result["subject"]
    if subject is not None:
        lines.extend([f"input_id: {safe(subject['input_id'])}", f"artifact_kind: {safe(subject['kind'])}", f"fingerprint: {safe(subject['fingerprint'] or 'not derived')}"])
    for item in result["violations"]:
        lines.append(f"VIOLATION {safe(item['rule'])} {safe(item['path'])}: {safe(item['message'])}")
    for item in result["findings"]:
        lines.append(f"FINDING {safe(item['status'])} {safe(item['path'])}: {safe(item['reason'])}")
    lines.extend(f"MATCH {safe(item['input_id'])} {safe(item['kind'])} {safe(item['fingerprint'] or 'not derived')}" for item in result["matches"])
    lines.append(f"records: {len(result['records'])}")
    for item in result["records"]:
        record = json.dumps(
            item["record"], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        )
        lines.append(
            f"RECORD {safe(item['source_input_id'])} "
            f"{safe(item['record_kind'])}: {safe(record)}"
        )
    return "\n".join(lines)


def _read_request_bytes(path: Path, limit: int = _MAX_REQUEST_BYTES) -> bytes:
    """Open once, refuse special streams, and bound allocation during read."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("request must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            data = stream.read(limit + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > limit:
        raise ValueError(
            f"request exceeds the {_MAX_REQUEST_MIB} MiB CLI input limit"
        )
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
