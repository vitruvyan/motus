"""SQLite persistence adapter for GraphState storage."""

import sqlite3
import json
import logging
from pathlib import Path
from typing import Optional, Sequence
from datetime import datetime

from axis.persistence.protocol import PersistenceProvider
from axis.state import GraphState

logger = logging.getLogger(__name__)


class SQLiteAdapter(PersistenceProvider):
    """SQLite-based persistence adapter for GraphState storage.

    Uses SQLite database to store GraphState instances with efficient querying
    by timestamp. Schema auto-creates on first connection.

    Args:
        db_path: Path to SQLite database file (default: "./axis_data/axis.db")
    """

    def __init__(self, db_path: str = "./axis_data/axis.db"):
        """Initialize SQLite adapter with database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        """Create database schema if it doesn't exist."""
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    intent TEXT,
                    data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_created_at
                ON traces(created_at DESC)
            """)
            self.conn.commit()
            logger.info(f"SQLite schema initialized at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize SQLite schema: {e}")
            raise

    def save(self, state: GraphState) -> None:
        """Save a GraphState to SQLite database.

        Idempotent operation - same trace_id overwrites existing entry.

        Args:
            state: GraphState to save
        """
        try:
            data = json.dumps(state.to_dict())
            with self.conn:
                self.conn.execute("""
                    INSERT OR REPLACE INTO traces (trace_id, intent, data, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (state.trace_id, state.intent, data))
            logger.debug(f"Saved trace {state.trace_id}")
        except (sqlite3.Error, json.JSONEncodeError) as e:
            logger.error(f"Failed to save trace {state.trace_id}: {e}")
            raise

    def load(self, trace_id: str) -> Optional[GraphState]:
        """Load a GraphState by trace_id.

        Args:
            trace_id: Trace identifier to load

        Returns:
            GraphState if found, None otherwise
        """
        try:
            cursor = self.conn.execute("""
                SELECT data FROM traces WHERE trace_id = ?
            """, (trace_id,))
            row = cursor.fetchone()

            if row is None:
                logger.debug(f"Trace {trace_id} not found")
                return None

            data = json.loads(row[0])
            state = GraphState.from_dict(data)
            logger.debug(f"Loaded trace {trace_id}")
            return state

        except (sqlite3.Error, json.JSONDecodeError) as e:
            logger.error(f"Failed to load trace {trace_id}: {e}")
            return None

    def query_by_timestamp(
        self,
        start: datetime,
        end: datetime
    ) -> Sequence[GraphState]:
        """Query GraphStates within timestamp range.

        Uses database timestamp for efficient filtering.

        Args:
            start: Start of timestamp range (inclusive)
            end: End of timestamp range (inclusive)

        Returns:
            Sequence of GraphState instances within range
        """
        try:
            cursor = self.conn.execute("""
                SELECT data FROM traces
                WHERE created_at >= ? AND created_at <= ?
                ORDER BY created_at DESC
            """, (start.isoformat(), end.isoformat()))

            states = []
            for row in cursor:
                try:
                    data = json.loads(row[0])
                    state = GraphState.from_dict(data)
                    states.append(state)
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Skipping corrupted trace data: {e}")
                    continue

            logger.debug(f"Queried {len(states)} traces between {start} and {end}")
            return states

        except sqlite3.Error as e:
            logger.error(f"Failed to query traces by timestamp: {e}")
            return []

    def list_traces(self, limit: int = 100) -> Sequence[str]:
        """List trace IDs, most recent first.

        Args:
            limit: Maximum number of trace IDs to return

        Returns:
            Sequence of trace IDs
        """
        try:
            cursor = self.conn.execute("""
                SELECT trace_id FROM traces
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))

            trace_ids = [row[0] for row in cursor]
            logger.debug(f"Listed {len(trace_ids)} traces")
            return trace_ids

        except sqlite3.Error as e:
            logger.error(f"Failed to list traces: {e}")
            return []

    def delete(self, trace_id: str) -> bool:
        """Delete a trace by trace_id.

        Args:
            trace_id: Trace identifier to delete

        Returns:
            True if trace existed and was deleted, False otherwise
        """
        try:
            cursor = self.conn.execute("""
                DELETE FROM traces WHERE trace_id = ?
            """, (trace_id,))

            deleted = cursor.rowcount > 0
            self.conn.commit()

            if deleted:
                logger.debug(f"Deleted trace {trace_id}")
            else:
                logger.debug(f"Trace {trace_id} not found for deletion")

            return deleted

        except sqlite3.Error as e:
            logger.error(f"Failed to delete trace {trace_id}: {e}")
            return False

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("SQLite connection closed")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def __del__(self):
        """Destructor - ensure connection is closed."""
        try:
            self.close()
        except Exception:
            pass  # Ignore errors during cleanup