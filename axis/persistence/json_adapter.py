"""File-based JSON persistence adapter for GraphState."""

import json
import logging
import os
from pathlib import Path
from typing import Optional, Sequence
from datetime import datetime

from axis.persistence.protocol import PersistenceProvider
from axis.state import GraphState

logger = logging.getLogger(__name__)


class JSONAdapter(PersistenceProvider):
    """File-based JSON persistence adapter for GraphState.

    Stores each GraphState as a JSON file in the traces directory.
    Uses atomic writes for data integrity.
    """

    def __init__(self, base_path: str = "./axis_data"):
        """
        Initialize the JSON adapter.

        Args:
            base_path: Directory where JSON files are stored.
                      Creates structure: base_path/traces/<trace_id>.json
        """
        self.base_path = Path(base_path)
        self.traces_dir = self.base_path / "traces"
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    def save(self, state: GraphState) -> None:
        """Save a GraphState to JSON file. Idempotent (same trace_id overwrites)."""
        try:
            data = state.to_dict()
            file_path = self.traces_dir / f"{state.trace_id}.json"

            # Atomic write: write to temp file, then rename
            temp_path = file_path.with_suffix('.tmp')
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            # Atomic rename
            temp_path.replace(file_path)

            logger.debug(f"Saved GraphState {state.trace_id} to {file_path}")

        except Exception as e:
            logger.error(f"Failed to save GraphState {state.trace_id}: {e}")
            raise

    def load(self, trace_id: str) -> Optional[GraphState]:
        """Load a GraphState by trace_id. Returns None if not found."""
        try:
            file_path = self.traces_dir / f"{trace_id}.json"

            if not file_path.exists():
                return None

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            state = GraphState.from_dict(data)
            logger.debug(f"Loaded GraphState {trace_id} from {file_path}")
            return state

        except json.JSONDecodeError as e:
            logger.error(f"Corrupted JSON file for trace {trace_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to load GraphState {trace_id}: {e}")
            return None

    def query_by_timestamp(
        self,
        start: datetime,
        end: datetime
    ) -> Sequence[GraphState]:
        """Query GraphStates within timestamp range."""
        try:
            results = []

            # List all JSON files
            for file_path in self.traces_dir.glob("*.json"):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    state = GraphState.from_dict(data)

                    # Find the earliest timestamp in events
                    if state.events:
                        earliest_timestamp = min(event.timestamp for event in state.events)
                        if start <= earliest_timestamp <= end:
                            results.append(state)
                    else:
                        # If no events, use current time as fallback (shouldn't happen)
                        logger.warning(f"No events in trace {state.trace_id}")

                except Exception as e:
                    logger.error(f"Error processing file {file_path}: {e}")
                    continue

            # Sort by earliest event timestamp (most recent first)
            results.sort(key=lambda s: min(e.timestamp for e in s.events) if s.events else datetime.min, reverse=True)

            return tuple(results)

        except Exception as e:
            logger.error(f"Failed to query by timestamp: {e}")
            return ()

    def list_traces(self, limit: int = 100) -> Sequence[str]:
        """List trace IDs, most recent first."""
        try:
            # Get all JSON files with their modification times
            files_with_mtime = []
            for file_path in self.traces_dir.glob("*.json"):
                try:
                    mtime = file_path.stat().st_mtime
                    trace_id = file_path.stem  # Remove .json extension
                    files_with_mtime.append((trace_id, mtime))
                except Exception as e:
                    logger.error(f"Error getting mtime for {file_path}: {e}")
                    continue

            # Sort by mtime (most recent first)
            files_with_mtime.sort(key=lambda x: x[1], reverse=True)

            # Extract trace IDs, limit results
            trace_ids = [trace_id for trace_id, _ in files_with_mtime[:limit]]

            return tuple(trace_ids)

        except Exception as e:
            logger.error(f"Failed to list traces: {e}")
            return ()

    def delete(self, trace_id: str) -> bool:
        """Delete a trace. Returns True if existed."""
        try:
            file_path = self.traces_dir / f"{trace_id}.json"

            if file_path.exists():
                file_path.unlink()
                logger.debug(f"Deleted trace {trace_id}")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Failed to delete trace {trace_id}: {e}")
            return False