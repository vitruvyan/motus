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

    Args:
        runner: AsyncRunner instance
        state: Initial GraphState

    Yields:
        ServerSentEvent for each node completion

    Example:
        from axis.streaming import AsyncRunner, stream_graph_execution

        runner = AsyncRunner(nodes=[...])

        async for event in stream_graph_execution(runner, state):
            print(event.encode())
            # Send to HTTP client via response.write()
    """
    event_id = 0

    # Start event
    yield ServerSentEvent(
        event="graph_start",
        data={
            "trace_id": state.trace_id,
            "intent": state.intent,
        },
        id=str(event_id),
    )
    event_id += 1

    # Stream node executions
    async for current_state in runner.stream(state):
        # Extract last event (most recent node execution)
        if current_state.events:
            last_event = current_state.events[-1]

            yield ServerSentEvent(
                event="node_completed",
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

    # End event
    yield ServerSentEvent(
        event="graph_end",
        data={
            "trace_id": state.trace_id,
            "facts_count": len(current_state.facts),
            "decisions_count": len(current_state.decisions),
        },
        id=str(event_id),
    )