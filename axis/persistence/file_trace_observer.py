"""FileTraceObserver — persists a trace the moment its graph ends."""

from pathlib import Path

from axis.events import EventType
from axis.state import GraphState


class FileTraceObserver:
    """
    Runner observer that writes <directory>/<trace_id>.json on GRAPH_END.

    Attach it like any other observer (runner.attach(...) or bus=...).
    Atomic write — temp file then rename — same idiom as
    axis.persistence.json_adapter.JSONAdapter.save.
    """

    def __init__(self, directory: str):
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    def observe(self, event_type: str, state: GraphState, **kwargs) -> None:
        if event_type != EventType.GRAPH_END.value:
            return
        target = self._directory / f"{state.trace_id}.json"
        temp = target.with_suffix(".tmp")
        temp.write_text(state.to_json(), encoding="utf-8")
        temp.replace(target)
