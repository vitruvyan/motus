"""FileTraceObserver — persists a trace as its graph runs, not just on
success."""

import logging
import os
import re
from pathlib import Path

from axis.events import EventType
from axis.state import GraphState

logger = logging.getLogger(__name__)

# GraphState.empty()/with trace_id accepts any string — including one built
# from unvalidated request input by a consumer (a chat trace_id embedding a
# URL parameter, say). The filename on disk must never be that string
# unflattened: only these characters survive, everything else (including
# '/' and '\') becomes '-', so the on-disk name can never contain a path
# separator and can never traverse out of `directory`. The JSON *content*
# still carries the real, unflattened trace_id — only the filename changes.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename(trace_id: str) -> str:
    flattened = _UNSAFE_FILENAME_CHARS.sub("-", trace_id)
    return f"{flattened or 'trace'}.json"


class FileTraceObserver:
    """
    Runner observer that persists a trace under `directory` as
    <flattened-trace_id>.json.

    Writes on GRAPH_END (the final trace) AND on ERROR (an intermediate
    snapshot) — a STRICT abort never reaches GRAPH_END (see NodeFailed's
    docstring), so without the ERROR write the one persistence primitive
    this kernel ships would be unable to persist the one class of run
    it's named after. Both writes target the same filename; the later one
    (whichever fires) wins via atomic replace.

    `critical = True`: unlike a metrics/logging observer, this one IS the
    audit evidence. Runner._notify/AsyncRunner._notify re-raise its
    exceptions instead of swallowing them — a trace_id with a path
    separator, a directory that got removed after construction, a full
    disk: all now surface to the caller instead of the run reporting
    success with nothing on disk.
    """

    critical = True

    def __init__(self, directory: str):
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    def observe(self, event_type: str, state: GraphState, **kwargs) -> None:
        if event_type not in (EventType.GRAPH_END.value, EventType.ERROR.value):
            return

        # Re-create per write, not just at construction: a long-lived
        # observer must survive its directory being removed between runs.
        self._directory.mkdir(parents=True, exist_ok=True)

        target = self._directory / _safe_filename(state.trace_id)
        temp = target.parent / f"{target.name}.{os.getpid()}.tmp"

        with open(temp, "w", encoding="utf-8") as f:
            f.write(state.to_json())
            f.flush()
            os.fsync(f.fileno())  # durable before the rename claims it is

        temp.replace(target)  # same filesystem as target: atomic
