"""
Axis Streaming Layer - Real-time execution streaming.

Provides:
- AsyncRunner: Async version of Runner with streaming
- ConcurrentRunner: Parallel node execution

Example:
    from axis.streaming import AsyncRunner
    
    async def my_node(state: GraphState) -> GraphState:
        await asyncio.sleep(0.1)
        return state.with_fact(...)
    
    runner = AsyncRunner(nodes=[my_node])
    
    # Stream updates
    async for state in runner.stream(initial_state):
        print(f"Progress: {len(state.events)} events")
"""

from axis.streaming.async_runner import (
    AsyncRunner,
    ConcurrentRunner,
)

from axis.streaming.event_stream import (
    stream_graph_execution,
    ServerSentEvent,
)

from axis.streaming.websocket import (
    WebSocketStreamHandler,
    WebSocketMessage,
)

__all__ = [
    # Async Execution
    "AsyncRunner",
    "ConcurrentRunner",
    # Server-Sent Events
    "stream_graph_execution",
    "ServerSentEvent",
    # WebSocket
    "WebSocketStreamHandler",
    "WebSocketMessage",
]
