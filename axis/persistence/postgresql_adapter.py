"""PostgreSQL persistence adapter for GraphState storage.

Phase 2.3 - PostgreSQL Backend
Provides efficient JSONB-based storage with GIN indexing for audit trails.
"""

import logging
from typing import Optional, Sequence
from datetime import datetime
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from axis.state import GraphState
from axis.persistence.protocol import PersistenceProvider


logger = logging.getLogger(__name__)


class PostgreSQLAdapter(PersistenceProvider):
    """PostgreSQL adapter using JSONB for efficient GraphState storage.

    Features:
    - JSONB native type for structured data storage
    - GIN indexing for fast JSON queries
    - Connection pooling for concurrent access
    - Automatic schema creation
    - Parameterized queries for security

    Args:
        host: PostgreSQL host (default: localhost)
        port: PostgreSQL port (default: 5432)
        database: Database name (default: axis)
        user: Database user (default: axis)
        password: Database password (default: "")
        pool_size: Connection pool size (default: 5)
        schema_name: Schema name (default: public)

    Note:
        Requires psycopg2-binary>=2.9.0 (external dependency)
        Documented in orders/requirements.txt
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "axis",
        user: str = "axis",
        password: str = "",
        pool_size: int = 5,
        schema_name: str = "public",
    ):
        """Initialize PostgreSQL adapter with connection pooling."""
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.pool_size = pool_size
        self.schema_name = schema_name

        # Initialize connection pool
        self.pool = pool.SimpleConnectionPool(
            1, pool_size,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )

        # Initialize schema on first connection
        self._init_schema()

    def _init_schema(self) -> None:
        """Create database schema if it doesn't exist."""
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                # Set schema if not public
                if self.schema_name != "public":
                    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema_name}")
                    cur.execute(f"SET search_path TO {self.schema_name}")

                # Create traces table
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self.schema_name}.traces (
                        trace_id TEXT PRIMARY KEY,
                        intent TEXT,
                        data JSONB NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)

                # Create indexes
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_traces_created_at
                    ON {self.schema_name}.traces(created_at DESC)
                """)

                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_traces_data_gin
                    ON {self.schema_name}.traces USING GIN (data)
                """)

            conn.commit()
            logger.info(f"PostgreSQL schema initialized in database '{self.database}'")

        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL schema: {e}")
            raise
        finally:
            if conn:
                self.pool.putconn(conn)

    def _get_connection(self):
        """Get a connection from the pool."""
        return self.pool.getconn()

    def _put_connection(self, conn):
        """Return connection to the pool."""
        self.pool.putconn(conn)

    def save(self, state: GraphState) -> None:
        """Save GraphState to PostgreSQL using JSONB.

        Uses INSERT ... ON CONFLICT for idempotent updates.
        """
        conn = None
        try:
            conn = self._get_connection()
            with conn.cursor() as cur:
                if self.schema_name != "public":
                    cur.execute(f"SET search_path TO {self.schema_name}")

                # Convert state to dict for JSONB storage
                data = state.to_dict()

                # Insert or update
                cur.execute(f"""
                    INSERT INTO {self.schema_name}.traces (trace_id, intent, data, updated_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (trace_id) DO UPDATE SET
                        intent = EXCLUDED.intent,
                        data = EXCLUDED.data,
                        updated_at = NOW()
                """, (state.trace_id, state.intent, psycopg2.extras.Json(data)))

            conn.commit()
            logger.debug(f"Saved GraphState with trace_id: {state.trace_id}")

        except Exception as e:
            logger.error(f"Failed to save GraphState {state.trace_id}: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                self._put_connection(conn)

    def load(self, trace_id: str) -> Optional[GraphState]:
        """Load GraphState from PostgreSQL by trace_id."""
        conn = None
        try:
            conn = self._get_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if self.schema_name != "public":
                    cur.execute(f"SET search_path TO {self.schema_name}")

                cur.execute(f"""
                    SELECT data FROM {self.schema_name}.traces
                    WHERE trace_id = %s
                """, (trace_id,))

                row = cur.fetchone()
                if row:
                    # PostgreSQL returns JSONB as dict directly
                    data = row['data']
                    state = GraphState.from_dict(data)
                    logger.debug(f"Loaded GraphState with trace_id: {trace_id}")
                    return state
                else:
                    logger.debug(f"GraphState not found: {trace_id}")
                    return None

        except Exception as e:
            logger.error(f"Failed to load GraphState {trace_id}: {e}")
            raise
        finally:
            if conn:
                self._put_connection(conn)

    def query_by_timestamp(
        self,
        start: datetime,
        end: datetime
    ) -> Sequence[GraphState]:
        """Query GraphStates within timestamp range using indexed column."""
        conn = None
        try:
            conn = self._get_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if self.schema_name != "public":
                    cur.execute(f"SET search_path TO {self.schema_name}")

                cur.execute(f"""
                    SELECT data FROM {self.schema_name}.traces
                    WHERE created_at BETWEEN %s AND %s
                    ORDER BY created_at DESC
                """, (start, end))

                rows = cur.fetchall()
                states = []
                for row in rows:
                    data = row['data']
                    state = GraphState.from_dict(data)
                    states.append(state)

                logger.debug(f"Queried {len(states)} GraphStates between {start} and {end}")
                return states

        except Exception as e:
            logger.error(f"Failed to query GraphStates by timestamp: {e}")
            raise
        finally:
            if conn:
                self._put_connection(conn)

    def list_traces(self, limit: int = 100) -> Sequence[str]:
        """List trace IDs ordered by most recent first."""
        conn = None
        try:
            conn = self._get_connection()
            with conn.cursor() as cur:
                if self.schema_name != "public":
                    cur.execute(f"SET search_path TO {self.schema_name}")

                cur.execute(f"""
                    SELECT trace_id FROM {self.schema_name}.traces
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (limit,))

                rows = cur.fetchall()
                trace_ids = [row[0] for row in rows]

                logger.debug(f"Listed {len(trace_ids)} trace IDs")
                return trace_ids

        except Exception as e:
            logger.error(f"Failed to list traces: {e}")
            raise
        finally:
            if conn:
                self._put_connection(conn)

    def delete(self, trace_id: str) -> bool:
        """Delete a trace by trace_id. Returns True if existed."""
        conn = None
        try:
            conn = self._get_connection()
            with conn.cursor() as cur:
                if self.schema_name != "public":
                    cur.execute(f"SET search_path TO {self.schema_name}")

                cur.execute(f"""
                    DELETE FROM {self.schema_name}.traces
                    WHERE trace_id = %s
                """, (trace_id,))

                deleted = cur.rowcount > 0
                conn.commit()

                if deleted:
                    logger.debug(f"Deleted GraphState with trace_id: {trace_id}")
                else:
                    logger.debug(f"GraphState not found for deletion: {trace_id}")

                return deleted

        except Exception as e:
            logger.error(f"Failed to delete GraphState {trace_id}: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                self._put_connection(conn)

    def query_by_jsonb(self, jsonb_query: dict, limit: int = 100) -> Sequence[GraphState]:
        """Query GraphStates using JSONB containment operator.

        Args:
            jsonb_query: JSONB query dict (e.g., {"intent": "specific_value"})
            limit: Maximum number of results

        Returns:
            Sequence of matching GraphStates
        """
        conn = None
        try:
            conn = self._get_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if self.schema_name != "public":
                    cur.execute(f"SET search_path TO {self.schema_name}")

                cur.execute(f"""
                    SELECT data FROM {self.schema_name}.traces
                    WHERE data @> %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (psycopg2.extras.Json(jsonb_query), limit))

                rows = cur.fetchall()
                states = []
                for row in rows:
                    data = row['data']
                    state = GraphState.from_dict(data)
                    states.append(state)

                logger.debug(f"JSONB query returned {len(states)} results")
                return states

        except Exception as e:
            logger.error(f"Failed JSONB query: {e}")
            raise
        finally:
            if conn:
                self._put_connection(conn)

    def close(self) -> None:
        """Close all connections in the pool."""
        if hasattr(self, 'pool') and self.pool:
            self.pool.closeall()
            logger.info("PostgreSQL connection pool closed")

    def __del__(self):
        """Cleanup on garbage collection."""
        self.close()
