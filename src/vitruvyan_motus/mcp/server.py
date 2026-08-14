"""The MCP binding, and nothing else.

Everything this module exposes is computed in ``tools`` and ``diagnose``, which
are stdlib-only and testable in an installation with no SDK present. That split
is deliberate: the derivation rule is the valuable part of ADR-022 and the
transport is not, so the transport is the only thing here that can break when
the SDK moves.

Started with ``python -m vitruvyan_motus.mcp.server``, or by an MCP client
configured to run that command. Nothing imports this module on any runtime
path.
"""

from __future__ import annotations

from typing import Any

from . import tools
from .diagnose import diagnose as _diagnose

__all__ = ["build", "main"]


def build() -> Any:
    """An ``MCPServer`` carrying the ADR-022 surface.

    Imported inside the function so that this module can be read, and its
    docstrings tested, without the SDK installed.
    """
    from mcp.server import MCPServer

    from vitruvyan_motus import __version__

    server = MCPServer(
        name="vitruvyan-motus",
        version=__version__,
        # A tool description and this string are the one place in this
        # component where text crosses the wire without being quoted from a
        # source: the protocol requires them before any tool has run, so there
        # is nothing yet to derive them from. They are therefore held to a
        # narrower rule, asserted by test: they say what an argument is and
        # what comes back, and they make no claim about the protocol's content
        # or about this server's own honesty. The previous version asserted
        # "Nothing here is written from memory" — a sentence written from
        # memory, in the server, which a round then showed was also untrue of
        # a hardcoded ranking.
        instructions=(
            "Each answer carries either the repository source it was quoted "
            "from, or the code that produced it and a command that runs it "
            "again. `motus_diagnose` takes a filesystem path."),
    )

    @server.tool(name="motus_classify")
    def motus_classify(description: str) -> str:
        """Report which node-protocol terms a description contains, with the table."""
        return tools.classify(description).render()

    @server.tool(name="motus_review_graph")
    def motus_review_graph(spec: dict) -> str:
        """Run GraphSpec.from_dict on a spec and report what it returned."""
        return tools.review_graph(spec).render()

    @server.tool(name="motus_review_node")
    def motus_review_node(source: str, effect_class: str | None = None) -> str:
        """Report ambient draws and a missing idempotency key in a node's source."""
        return tools.review_node(source, effect_class=effect_class).render()

    @server.tool(name="motus_explain")
    def motus_explain(error: str) -> str:
        """Return the docstring and ancestry of a named Motus exception class."""
        return tools.explain(error).render()

    @server.tool(name="motus_start_here")
    def motus_start_here() -> str:
        """Return two shipped example programs, verbatim."""
        return tools.start_here().render()

    @server.tool(name="motus_where")
    def motus_where(intent: str) -> str:
        """Return every kernel module with its own one-line docstring."""
        return tools.where(intent).render()

    @server.tool(name="motus_diagnose")
    def motus_diagnose(path: str, symptom: str = "") -> str:
        """Run the shipped validator over an artefact named by PATH.

        PATH is a filesystem path this server process can open. Artefact
        content is refused.
        """
        return _diagnose(path, symptom=symptom).render()

    return server


def main() -> int:                 # pragma: no cover - a transport loop
    build().run()
    return 0


if __name__ == "__main__":         # pragma: no cover - a transport loop
    raise SystemExit(main())
