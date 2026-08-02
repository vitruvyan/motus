"""
Server-Sent Events (SSE) streaming for Axis graph execution.

Provides ServerSentEvent formatter and stream_graph_execution function
for real-time streaming of graph execution updates via SSE.
"""

from typing import AsyncIterator, Optional
from axis.state import GraphState
import json


class ServerSentEvent:
    """
    Server-Sent Event (SSE) formatter.

    SSE format:
        event: node_completed
        data: {"trace_id": "...", "node": "...", "facts_count": 5}

    """

    def __init__(
        self,
        event: str,
        data: dict,
        id: Optional[str] = None,
    ):
        self.event = event
        self.data = data
        self.id = id

    def encode(self) -> str:
        """
        Encode as SSE format.

        Returns:
            SSE-formatted string
        """
        lines = []

        if self.id:
            lines.append(f"id: {self.id}")

        lines.append(f"event: {self.event}")

        # Serialize data as JSON
        data_json = json.dumps(self.data, default=str)
        lines.append(f"data: {data_json}")

        # SSE requires blank line after event
        lines.append("")

        return "\n".join(lines) + "\n"


async def stream_graph_execution(
    runner: 'AsyncRunner',
    state: GraphState,
) -> AsyncIterator[ServerSentEvent]:
    """
    Stream graph execution as Server-Sent Events.

    One SSE frame per GraphState runner.stream() yields, labeled with the
    ACTUAL event that produced it (event.event_type.value: "graph_start",
    "node_started", "node_completed", "node_skipped", "error",
    "graph_end") — runner.stream() already yields once after GRAPH_START
    and once after GRAPH_END, so a synthetic wrapper frame around the
    loop would double them. A 2-node run produces exactly 4 frames:
    graph_start, node_completed, node_completed, graph_end.

    Under Policy.STRICT, a node failure raises NodeFailed out of the
    async generator instead of yielding an "error" frame — catch it at
    the call site and read `.state` for the trace if you need it.

    Args:
        runner: AsyncRunner instance
        state: Initial GraphState

    Yields:
        ServerSentEvent per lifecycle event

    Example:
        from axis.streaming import AsyncRunner, stream_graph_execution

        runner = AsyncRunner(nodes=[...])

        async for event in stream_graph_execution(runner, state):
            print(event.encode())
            # Send to HTTP client via response.write()
    """
    event_id = 0

    async for current_state in runner.stream(state):
        if not current_state.events:
            continue
        last_event = current_state.events[-1]

        yield ServerSentEvent(
            event=last_event.event_type.value,
            data={
                "trace_id": current_state.trace_id,
                "node_name": last_event.node_name,
                "event_type": last_event.event_type.value,
                "facts_count": len(current_state.facts),
                "decisions_count": len(current_state.decisions),
            },
            id=str(event_id),
        )
        event_id += 1