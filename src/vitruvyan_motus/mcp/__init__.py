"""The integration MCP (ADR-022) — off until installed, and derived when on.

Importing this package imports no server and no third-party code. The MCP SDK
binding lives in ``.server`` and is imported only when a server is actually
started, so ``vitruvyan_motus.mcp.tools`` can be used — and tested — in an
installation that has no SDK at all.

Nothing on any runtime path imports this package. That is ADR-021 decision 1's
house rule kept: a library that grows a server by default has become a service,
and nobody decided that.
"""

from __future__ import annotations

__all__ = ["answers", "diagnose", "protocol", "sources", "tools"]
