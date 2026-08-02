"""Persistence layer for GraphState storage and retrieval.

Phase 2.3 - Week 1-2
Provides adapters for JSON, SQLite, PostgreSQL, and Qdrant backends.

Imports are lazy (PEP 562 module __getattr__): QdrantAdapter and
PostgreSQLAdapter pull in httpx / psycopg2 at their own module's import
time, and not every consumer of this kernel has those installed.
`from axis.persistence import FileTraceObserver` (or JSONAdapter, or
SQLiteAdapter — stdlib only) must not require them.
"""

import importlib

__all__ = [
    "PersistenceProvider",
    "JSONAdapter",
    "SQLiteAdapter",
    "PostgreSQLAdapter",
    "QdrantAdapter",
    "FileTraceObserver",
]

_SUBMODULE_BY_NAME = {
    "PersistenceProvider": "protocol",
    "JSONAdapter": "json_adapter",
    "SQLiteAdapter": "sqlite_adapter",
    "PostgreSQLAdapter": "postgresql_adapter",
    "QdrantAdapter": "qdrant_adapter",
    "FileTraceObserver": "file_trace_observer",
}


def __getattr__(name: str):
    submodule = _SUBMODULE_BY_NAME.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{submodule}", __name__)
    value = getattr(module, name)
    globals()[name] = value  # cache: subsequent lookups skip __getattr__
    return value


def __dir__():
    return sorted(__all__)
