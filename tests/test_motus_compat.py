from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

import vitruvyan_motus
import vitruvyan_motus.compat as compat


def test_native_and_legacy_decision_names_cannot_be_confused():
    assert vitruvyan_motus.Decision is not compat.LegacyDecision
    assert "Decision" not in compat.__all__
    assert not hasattr(compat, "Decision")


def test_compatibility_view_is_self_contained_and_does_not_import_axis():
    script = (
        "import sys; import vitruvyan_motus.compat as c; "
        "assert 'axis' not in sys.modules; "
        "assert set(c.__all__) == {"
        "'GraphState','Runner','Policy','NodeFailed','Fact','LegacyDecision',"
        "'Rejection','FileTraceObserver','retry','ConcurrentRunner'}"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


def test_concurrent_failure_notifies_the_file_observer_before_strict_abort(tmp_path):
    def fails(state):
        raise RuntimeError("boom")

    runner = compat.ConcurrentRunner([fails], policy=compat.Policy.STRICT)
    runner.attach(compat.FileTraceObserver(str(tmp_path)))
    with pytest.raises(compat.NodeFailed):
        asyncio.run(runner.run(compat.GraphState.empty("concurrent-failure")))
    persisted = list(tmp_path.glob("*.json"))
    assert len(persisted) == 1
    restored = compat.GraphState.from_json(persisted[0].read_text(encoding="utf-8"))
    assert any(event.event_type.value == "error" for event in restored.events)
