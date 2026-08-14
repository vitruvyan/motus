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
        instructions=(
            "Every answer names the repository source it was quoted from, or "
            "the shipped code that produced it and the command that reproduces "
            "it. Nothing here is written from memory. `motus_diagnose` takes a "
            "filesystem path and refuses artefact content."),
    )

    @server.tool(name="motus_classify")
    def motus_classify(description: str) -> str:
        """Which effect class the node protocol gives an operation, and its cost."""
        return tools.classify(description).render()

    @server.tool(name="motus_review_graph")
    def motus_review_graph(spec: dict) -> str:
        """The verdict of the code that will refuse this GraphSpec at runtime."""
        return tools.review_graph(spec).render()

    @server.tool(name="motus_review_node")
    def motus_review_node(source: str, effect_class: str | None = None) -> str:
        """What the protocol says about the calls a node's source makes."""
        return tools.review_node(source, effect_class=effect_class).render()

    @server.tool(name="motus_explain")
    def motus_explain(error: str) -> str:
        """What a Motus error means, from the class that raises it."""
        return tools.explain(error).render()

    @server.tool(name="motus_start_here")
    def motus_start_here() -> str:
        """The shape of a Motus program, as the shipped example writes it."""
        return tools.start_here().render()

    @server.tool(name="motus_where")
    def motus_where(intent: str) -> str:
        """Which module of the kernel already owns this kind of code."""
        return tools.where(intent).render()

    @server.tool(name="motus_diagnose")
    def motus_diagnose(path: str, symptom: str = "") -> str:
        """Run the shipped code over an artefact named by PATH and report it.

        PATH is a filesystem path this server process can open. Artefact
        content is refused (ADR-022 decision 4e): a path is meaningless to a
        server on another machine, so a remote deployment cannot work rather
        than working while it leaks the caller's evidence.
        """
        return _diagnose(path, symptom=symptom).render()

    return server


def main() -> int:                 # pragma: no cover - a transport loop
    build().run()
    return 0


if __name__ == "__main__":         # pragma: no cover - a transport loop
    raise SystemExit(main())
