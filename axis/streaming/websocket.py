from typing import Optional, AsyncIterator
from dataclasses import dataclass
import json
import asyncio
import logging

logger = logging.getLogger(__name__)

@dataclass
class WebSocketMessage:
    """
    WebSocket message structure.

    `type` is the actual axis.events.EventType value that produced the
    message — graph_start, node_started, node_completed, node_skipped,
    error, graph_end — plus two control types this handler adds:
    graph_cancelled and control (pause/resume/cancel from the client).
    """
    
    type: str
    data: dict
    
    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps({
            "type": self.type,
            "data": self.data,
        }, default=str)
    
    @classmethod
    def from_json(cls, json_str: str) -> 'WebSocketMessage':
        """Parse from JSON."""
        obj = json.loads(json_str)
        return cls(
            type=obj["type"],
            data=obj["data"],
        )

class WebSocketStreamHandler:
    """
    WebSocket handler for graph execution streaming.
    
    Supports:
    - Real-time state updates
    - Bidirectional communication
    - Control messages (pause/resume)
    
    Example:
        handler = WebSocketStreamHandler()
        
        # Server side:
        async for message in handler.stream(runner, state):
            await websocket.send(message.to_json())
        
        # Can also receive control messages:
        control = WebSocketMessage.from_json(await websocket.receive())
        if control.type == "pause":
            handler.pause()
    """
    
    def __init__(self):
        self.paused = False
        self.cancelled = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused initially
    
    async def stream(
        self,
        runner: 'AsyncRunner',
        state: 'GraphState',
    ) -> AsyncIterator[WebSocketMessage]:
        """
        Stream graph execution as WebSocket messages.

        One message per GraphState runner.stream() yields, `type`d with
        the ACTUAL event that produced it (event.event_type.value) —
        runner.stream() already yields once after GRAPH_START and once
        after GRAPH_END, so a synthetic wrapper message around the loop
        would double them.

        Under Policy.STRICT, a node failure raises NodeFailed out of
        this async generator instead of yielding an "error" message —
        catch it at the call site and read `.state` if you need the
        trace.

        Args:
            runner: AsyncRunner instance
            state: Initial GraphState

        Yields:
            WebSocketMessage per lifecycle event
        """
        async for current_state in runner.stream(state):
            # Check for pause
            await self._pause_event.wait()

            # Check for cancellation
            if self.cancelled:
                yield WebSocketMessage(
                    type="graph_cancelled",
                    data={"trace_id": state.trace_id},
                )
                break

            if not current_state.events:
                continue
            last_event = current_state.events[-1]

            yield WebSocketMessage(
                type=last_event.event_type.value,
                data={
                    "trace_id": current_state.trace_id,
                    "node_name": last_event.node_name,
                    "event_type": last_event.event_type.value,
                    "facts_count": len(current_state.facts),
                    "decisions_count": len(current_state.decisions),
                    "rejections_count": len(current_state.rejections),
                },
            )
    
    def pause(self):
        """Pause execution."""
        self.paused = True
        self._pause_event.clear()
        logger.info("Execution paused")
    
    def resume(self):
        """Resume execution."""
        self.paused = False
        self._pause_event.set()
        logger.info("Execution resumed")
    
    def cancel(self):
        """Cancel execution."""
        self.cancelled = True
        self._pause_event.set()  # Unblock if paused
        logger.info("Execution cancelled")
    
    def handle_control_message(self, message: WebSocketMessage):
        """
        Handle control message from client.
        
        Args:
            message: WebSocketMessage with type "control"
        """
        if message.type != "control":
            return
        
        action = message.data.get("action")
        
        if action == "pause":
            self.pause()
        elif action == "resume":
            self.resume()
        elif action == "cancel":
            self.cancel()
        else:
            logger.warning(f"Unknown control action: {action}")