from __future__ import annotations

import json
import runpy
import sys
import urllib.request
from pathlib import Path


class _Response:
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "json": json.dumps(
                    {"babel_status": "success", "language_detected": "it"}
                ),
                "human": "risposta " * 10,
                "route_taken": "answer",
                "orthodoxy_status": "ok",
                "correlation_id": "probe-test",
            }
        ).encode()


def test_pipeline_probe_records_latency_at_an_integer_scale(
    monkeypatch, capsys
) -> None:
    """The live-release probe must itself satisfy trace schema 3.2/J4."""
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(sys, "argv", ["pipeline_query.py", "richiesta di prova"])

    runpy.run_path(str(Path("e2e/pipeline_query.py")), run_name="__main__")

    output = capsys.readouterr().out
    assert "nessuna violazione" in output
    assert "registra  latency_us = " in output
    assert "registra  latency_ms = " not in output
