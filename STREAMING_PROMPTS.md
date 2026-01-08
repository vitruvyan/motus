# Axis Phase 2.3 Week 7-8 - Streaming Prompts

**Context:** You are implementing streaming for Axis, a minimal cognitive graph kernel.  
**Architecture:** Immutable GraphState, frozen dataclasses, async execution support.  
**Goal:** Enable real-time streaming of graph execution with async/await, SSE, and WebSocket.

---

## PROMPT 1: AsyncRunner

**Task:** Implement async version of Runner for concurrent execution.

**File to create:** `axis/streaming/async_runner.py` (~300 lines)

**Pattern:** Async runner that executes nodes concurrently and streams updates.

**Requirements:**

1. **AsyncRunner class:**
```python
from typing import Sequence, Optional, Callable, AsyncIterator
from axis.state import GraphState, Event
from axis.node import Node
from axis.policy import Policy
from axis.events import EventType
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)

class AsyncRunner:
    """
    Async runner for concurrent Node execution.
    
    Similar to Runner but supports:
    - Async nodes (async def)
    - Concurrent execution (asyncio.gather)
    - Streaming updates (async generator)
    - Event notifications
    
    Example:
        async def async_node(state: GraphState) -> GraphState:
            await asyncio.sleep(0.1)  # Async I/O
            return state.with_fact(...)
        
        runner = AsyncRunner(nodes=[async_node])
        async for state in runner.stream(initial_state):
            print(f"Progress: {len(state.events)} events")
        
        # Or just get final result:
        result = await runner.run(initial_state)
    """
    
    def __init__(
        self,
        nodes: Sequence[Callable],
        policy: Policy = Policy.STRICT,
        bus: Optional['SynapticBus'] = None,
    ):
        """
        Args:
            nodes: Sequence of async or sync Node callables
            policy: Execution policy (STRICT or EXPLORATION)
            bus: Optional SynapticBus for event notification
        """
        self.nodes = nodes
        self.policy = policy
        self.bus = bus
    
    async def run(self, state: GraphState) -> GraphState:
        """
        Execute all nodes asynchronously and return final state.
        
        Args:
            state: Initial GraphState
        
        Returns:
            Final GraphState after all nodes
        """
        if self.bus:
            self.bus.notify(EventType.GRAPH_START, state)
        
        current_state = state
        
        for node in self.nodes:
            try:
                node_name = node.__name__
                
                if self.bus:
                    self.bus.notify(
                        EventType.PRE_NODE,
                        current_state,
                        node_name=node_name,
                    )
                
                # Execute node (async or sync)
                if asyncio.iscoroutinefunction(node):
                    next_state = await node(current_state)
                else:
                    next_state = node(current_state)
                
                # Record execution event
                event = Event(
                    event_type=EventType.NODE_EXECUTED,
                    node_name=node_name,
                    timestamp=datetime.now(),
                )
                current_state = next_state.with_event(event)
                
                if self.bus:
                    self.bus.notify(
                        EventType.POST_NODE,
                        current_state,
                        node_name=node_name,
                    )
            
            except Exception as e:
                logger.error(f"Node {node_name} failed: {e}")
                
                if self.bus:
                    self.bus.notify(
                        EventType.ERROR,
                        current_state,
                        node_name=node_name,
                        error=e,
                    )
                
                if self.policy == Policy.STRICT:
                    raise
                
                # Record error event in EXPLORATION mode
                event = Event(
                    event_type=EventType.ERROR,
                    node_name=node_name,
                    timestamp=datetime.now(),
                    metadata={"error": str(e)},
                )
                current_state = current_state.with_event(event)
        
        if self.bus:
            self.bus.notify(EventType.GRAPH_END, current_state)
        
        return current_state
    
    async def stream(self, state: GraphState) -> AsyncIterator[GraphState]:
        """
        Stream GraphState updates during execution.
        
        Yields GraphState after each node execution.
        
        Args:
            state: Initial GraphState
        
        Yields:
            GraphState after each node
        
        Example:
            async for current_state in runner.stream(initial_state):
                print(f"Events: {len(current_state.events)}")
        """
        if self.bus:
            self.bus.notify(EventType.GRAPH_START, state)
        
        current_state = state
        yield current_state  # Yield initial state
        
        for node in self.nodes:
            try:
                node_name = node.__name__
                
                if self.bus:
                    self.bus.notify(
                        EventType.PRE_NODE,
                        current_state,
                        node_name=node_name,
                    )
                
                # Execute node
                if asyncio.iscoroutinefunction(node):
                    next_state = await node(current_state)
                else:
                    next_state = node(current_state)
                
                # Record execution event
                event = Event(
                    event_type=EventType.NODE_EXECUTED,
                    node_name=node_name,
                    timestamp=datetime.now(),
                )
                current_state = next_state.with_event(event)
                
                if self.bus:
                    self.bus.notify(
                        EventType.POST_NODE,
                        current_state,
                        node_name=node_name,
                    )
                
                yield current_state  # Yield after each node
            
            except Exception as e:
                logger.error(f"Node {node_name} failed: {e}")
                
                if self.bus:
                    self.bus.notify(
                        EventType.ERROR,
                        current_state,
                        node_name=node_name,
                        error=e,
                    )
                
                if self.policy == Policy.STRICT:
                    raise
                
                # Record error and continue
                event = Event(
                    event_type=EventType.ERROR,
                    node_name=node_name,
                    timestamp=datetime.now(),
                    metadata={"error": str(e)},
                )
                current_state = current_state.with_event(event)
                yield current_state
        
        if self.bus:
            self.bus.notify(EventType.GRAPH_END, current_state)
```

2. **Concurrent execution support:**
```python
class ConcurrentRunner(AsyncRunner):
    """
    Runner that executes multiple nodes concurrently.
    
    Example:
        # Execute 3 independent nodes in parallel
        runner = ConcurrentRunner(nodes=[node1, node2, node3])
        result = await runner.run(state)
    """
    
    async def run(self, state: GraphState) -> GraphState:
        """Execute all nodes concurrently using asyncio.gather."""
        if self.bus:
            self.bus.notify(EventType.GRAPH_START, state)
        
        # Execute all nodes concurrently
        tasks = []
        for node in self.nodes:
            if asyncio.iscoroutinefunction(node):
                tasks.append(node(state))
            else:
                # Wrap sync function in async
                tasks.append(asyncio.to_thread(node, state))
        
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Merge results (simple merge: take first non-error)
            current_state = state
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    if self.policy == Policy.STRICT:
                        raise result
                    logger.warning(f"Node {i} failed: {result}")
                else:
                    # Merge state (combine facts, decisions, etc.)
                    current_state = self._merge_states(current_state, result)
            
            if self.bus:
                self.bus.notify(EventType.GRAPH_END, current_state)
            
            return current_state
        
        except Exception as e:
            if self.bus:
                self.bus.notify(EventType.ERROR, state, error=e)
            raise
    
    def _merge_states(self, state1: GraphState, state2: GraphState) -> GraphState:
        """
        Merge two GraphStates (simple union of collections).
        
        Note: This is a naive merge. Production may need conflict resolution.
        """
        return GraphState(
            trace_id=state1.trace_id,
            intent=state1.intent,
            facts=state1.facts + state2.facts,
            decisions=state1.decisions + state2.decisions,
            rejections=state1.rejections + state2.rejections,
            events=state1.events + state2.events,
        )
```

**Key principles:**
- Supports both async and sync nodes
- Streaming via async generator
- Immutability preserved
- Compatible with SynapticBus

**Acceptance Criteria:**
- [ ] run() executes nodes sequentially (async)
- [ ] stream() yields after each node
- [ ] Supports async def nodes
- [ ] Supports sync nodes (wrapped)
- [ ] ConcurrentRunner executes in parallel
- [ ] Policy.EXPLORATION continues on error
- [ ] SynapticBus integration works
- [ ] No external dependencies (stdlib asyncio)

---

## PROMPT 2: Server-Sent Events (SSE)

**Task:** Implement SSE streaming for real-time graph execution updates.

**File to create:** `axis/streaming/event_stream.py` (~200 lines)

**Pattern:** HTTP endpoint that streams graph execution as Server-Sent Events.

**Requirements:**

1. **SSE event formatter:**
```python
from typing import AsyncIterator
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
```

2. **HTTP handler (using aiohttp or similar):**
```python
# Example with Python's built-in http.server (basic)
# For production, use aiohttp or FastAPI

from http.server import BaseHTTPRequestHandler, HTTPServer
import asyncio

class SSEHandler(BaseHTTPRequestHandler):
    """HTTP handler for SSE endpoint."""
    
    runner: Optional['AsyncRunner'] = None
    initial_state: Optional[GraphState] = None
    
    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            
            # Stream events
            async def stream():
                async for event in stream_graph_execution(
                    self.runner,
                    self.initial_state,
                ):
                    self.wfile.write(event.encode().encode('utf-8'))
                    self.wfile.flush()
            
            # Run async code in sync context
            asyncio.run(stream())
        else:
            self.send_response(404)
            self.end_headers()

# Note: For production, use FastAPI:
"""
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.get("/stream")
async def stream_endpoint():
    async def event_generator():
        runner = AsyncRunner(nodes=[...])
        state = GraphState(trace_id="...")
        
        async for event in stream_graph_execution(runner, state):
            yield event.encode()
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
"""
```

**Key principles:**
- SSE standard format (event:, data:, id:)
- One event per node completion
- JSON data payload
- Unidirectional (server → client)
- Compatible with EventSource JavaScript API

**Acceptance Criteria:**
- [ ] ServerSentEvent.encode() produces valid SSE format
- [ ] stream_graph_execution() yields events for each node
- [ ] Events include trace_id, node_name, counts
- [ ] graph_start and graph_end events
- [ ] Compatible with EventSource API
- [ ] No external dependencies (stdlib only for core)

---

## PROMPT 3: WebSocket Support

**Task:** Implement bidirectional WebSocket streaming for interactive execution.

**File to create:** `axis/streaming/websocket.py` (~200 lines)

**Pattern:** WebSocket server for bidirectional communication during execution.

**Requirements:**

1. **WebSocket message protocol:**
```python
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
    
    Types:
    - state_update: GraphState update
    - node_start: Node execution starting
    - node_end: Node execution completed
    - error: Execution error
    - control: Control message (pause, resume, cancel)
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
        state: GraphState,
    ) -> AsyncIterator[WebSocketMessage]:
        """
        Stream graph execution as WebSocket messages.
        
        Args:
            runner: AsyncRunner instance
            state: Initial GraphState
        
        Yields:
            WebSocketMessage for each event
        """
        # Start message
        yield WebSocketMessage(
            type="graph_start",
            data={
                "trace_id": state.trace_id,
                "intent": state.intent,
            },
        )
        
        # Stream execution
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
            
            # State update
            if current_state.events:
                last_event = current_state.events[-1]
                
                yield WebSocketMessage(
                    type="state_update",
                    data={
                        "trace_id": current_state.trace_id,
                        "node_name": last_event.node_name,
                        "event_type": last_event.event_type.value,
                        "facts_count": len(current_state.facts),
                        "decisions_count": len(current_state.decisions),
                        "rejections_count": len(current_state.rejections),
                    },
                )
        
        # End message
        if not self.cancelled:
            yield WebSocketMessage(
                type="graph_end",
                data={
                    "trace_id": state.trace_id,
                    "total_events": len(current_state.events),
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
```

2. **WebSocket server example:**
```python
# Example using websockets library (requires: pip install websockets)
# For stdlib-only version, use basic socket + HTTP upgrade

"""
import websockets

async def websocket_handler(websocket, path):
    '''WebSocket handler for /ws endpoint.'''
    
    # Receive initial request
    init_message = await websocket.recv()
    msg = WebSocketMessage.from_json(init_message)
    
    if msg.type == "start":
        # Create runner and state from message
        runner = AsyncRunner(nodes=[...])
        state = GraphState(trace_id=msg.data["trace_id"])
        
        handler = WebSocketStreamHandler()
        
        # Stream execution
        async def send_updates():
            async for update in handler.stream(runner, state):
                await websocket.send(update.to_json())
        
        # Receive control messages
        async def receive_controls():
            async for message_str in websocket:
                control = WebSocketMessage.from_json(message_str)
                handler.handle_control_message(control)
        
        # Run both concurrently
        await asyncio.gather(
            send_updates(),
            receive_controls(),
        )

# Start server
async def main():
    async with websockets.serve(websocket_handler, "0.0.0.0", 8765):
        await asyncio.Future()  # Run forever

asyncio.run(main())
"""
```

**Key principles:**
- Bidirectional (client ↔ server)
- Control messages (pause/resume/cancel)
- JSON message format
- Async/await throughout
- Graceful cancellation

**Acceptance Criteria:**
- [ ] WebSocketMessage serialization works
- [ ] stream() yields messages for each node
- [ ] pause() blocks execution
- [ ] resume() unblocks execution
- [ ] cancel() terminates execution
- [ ] Control messages handled correctly
- [ ] No external dependencies for core (websockets optional)

---

## PROMPT 4: Tests & Integration

**Task:** Comprehensive test suite for streaming layer.

**File to create:** `tests/test_streaming.py` (~200 lines)

**Requirements:**

1. **Test fixtures:**
```python
import pytest
import asyncio
from axis.state import GraphState, Fact
from axis.streaming.async_runner import AsyncRunner, ConcurrentRunner
from axis.streaming.event_stream import stream_graph_execution, ServerSentEvent
from axis.streaming.websocket import WebSocketStreamHandler, WebSocketMessage
from datetime import datetime

@pytest.fixture
def sample_state():
    return GraphState(trace_id="test-stream-123")

async def async_node_1(state: GraphState) -> GraphState:
    """Async node for testing."""
    await asyncio.sleep(0.1)
    fact = Fact("key1", "value1", "async_node_1", datetime.now())
    return state.with_fact(fact)

async def async_node_2(state: GraphState) -> GraphState:
    """Another async node."""
    await asyncio.sleep(0.1)
    fact = Fact("key2", "value2", "async_node_2", datetime.now())
    return state.with_fact(fact)

def sync_node(state: GraphState) -> GraphState:
    """Sync node for testing."""
    fact = Fact("sync", "value", "sync_node", datetime.now())
    return state.with_fact(fact)

async def failing_node(state: GraphState) -> GraphState:
    """Node that raises error."""
    raise ValueError("Test async error")
```

2. **AsyncRunner tests:**
```python
@pytest.mark.asyncio
async def test_async_runner_executes_async_nodes(sample_state):
    """Test AsyncRunner executes async nodes."""
    runner = AsyncRunner(nodes=[async_node_1, async_node_2])
    result = await runner.run(sample_state)
    
    assert len(result.facts) == 2
    assert result.facts[0].key == "key1"
    assert result.facts[1].key == "key2"

@pytest.mark.asyncio
async def test_async_runner_stream_yields_states(sample_state):
    """Test AsyncRunner.stream() yields after each node."""
    runner = AsyncRunner(nodes=[async_node_1, async_node_2])
    
    states = []
    async for state in runner.stream(sample_state):
        states.append(state)
    
    # Should yield: initial, after node1, after node2
    assert len(states) >= 2
    assert len(states[-1].facts) == 2

@pytest.mark.asyncio
async def test_async_runner_handles_sync_nodes(sample_state):
    """Test AsyncRunner handles sync nodes."""
    runner = AsyncRunner(nodes=[sync_node, async_node_1])
    result = await runner.run(sample_state)
    
    assert len(result.facts) == 2

@pytest.mark.asyncio
async def test_async_runner_exploration_mode(sample_state):
    """Test AsyncRunner continues on error in EXPLORATION mode."""
    from axis.policy import Policy
    
    runner = AsyncRunner(
        nodes=[async_node_1, failing_node, async_node_2],
        policy=Policy.EXPLORATION,
    )
    result = await runner.run(sample_state)
    
    # Should execute node1 and node2, skip failing_node
    assert len(result.facts) >= 2

@pytest.mark.asyncio
async def test_concurrent_runner_executes_parallel(sample_state):
    """Test ConcurrentRunner executes nodes in parallel."""
    import time
    
    start = time.time()
    runner = ConcurrentRunner(nodes=[async_node_1, async_node_2])
    result = await runner.run(sample_state)
    duration = time.time() - start
    
    # Should take ~0.1s (parallel) not ~0.2s (sequential)
    assert duration < 0.15
    assert len(result.facts) == 2
```

3. **SSE tests:**
```python
@pytest.mark.asyncio
async def test_sse_stream_yields_events(sample_state):
    """Test SSE streaming yields events."""
    runner = AsyncRunner(nodes=[async_node_1])
    
    events = []
    async for event in stream_graph_execution(runner, sample_state):
        events.append(event)
    
    assert len(events) >= 2  # graph_start, node_completed, graph_end
    assert events[0].event == "graph_start"
    assert events[-1].event == "graph_end"

@pytest.mark.asyncio
async def test_sse_event_encoding():
    """Test ServerSentEvent encodes correctly."""
    event = ServerSentEvent(
        event="test",
        data={"key": "value"},
        id="123",
    )
    
    encoded = event.encode()
    
    assert "id: 123" in encoded
    assert "event: test" in encoded
    assert "data: " in encoded
    assert '"key": "value"' in encoded
    assert encoded.endswith("\n\n")
```

4. **WebSocket tests:**
```python
@pytest.mark.asyncio
async def test_websocket_stream_yields_messages(sample_state):
    """Test WebSocket streaming yields messages."""
    runner = AsyncRunner(nodes=[async_node_1])
    handler = WebSocketStreamHandler()
    
    messages = []
    async for message in handler.stream(runner, sample_state):
        messages.append(message)
    
    assert len(messages) >= 2
    assert messages[0].type == "graph_start"
    assert messages[-1].type == "graph_end"

@pytest.mark.asyncio
async def test_websocket_pause_resume(sample_state):
    """Test WebSocket pause/resume control."""
    runner = AsyncRunner(nodes=[async_node_1, async_node_2])
    handler = WebSocketStreamHandler()
    
    messages = []
    
    async def stream_task():
        async for message in handler.stream(runner, sample_state):
            messages.append(message)
    
    # Start streaming
    task = asyncio.create_task(stream_task())
    
    # Pause after short delay
    await asyncio.sleep(0.05)
    handler.pause()
    
    # Wait a bit (should be paused)
    await asyncio.sleep(0.1)
    paused_count = len(messages)
    
    # Resume
    handler.resume()
    await task
    
    # Should have more messages after resume
    assert len(messages) > paused_count

@pytest.mark.asyncio
async def test_websocket_cancel(sample_state):
    """Test WebSocket cancellation."""
    runner = AsyncRunner(nodes=[async_node_1, async_node_2])
    handler = WebSocketStreamHandler()
    
    messages = []
    
    async def stream_task():
        async for message in handler.stream(runner, sample_state):
            messages.append(message)
    
    # Start streaming
    task = asyncio.create_task(stream_task())
    
    # Cancel immediately
    handler.cancel()
    await task
    
    # Should have cancellation message
    assert any(m.type == "graph_cancelled" for m in messages)

@pytest.mark.asyncio
async def test_websocket_message_serialization():
    """Test WebSocketMessage serialization."""
    msg = WebSocketMessage(
        type="test",
        data={"key": "value"},
    )
    
    json_str = msg.to_json()
    parsed = WebSocketMessage.from_json(json_str)
    
    assert parsed.type == "test"
    assert parsed.data["key"] == "value"
```

5. **Integration test:**
```python
@pytest.mark.asyncio
async def test_streaming_with_synaptic_bus(sample_state):
    """Test streaming with SynapticBus integration."""
    from axis.synaptic_bus import SynapticBus
    from axis.observability.metrics import PrometheusMetrics
    
    bus = SynapticBus()
    metrics = PrometheusMetrics()
    bus.attach(metrics)
    
    runner = AsyncRunner(nodes=[async_node_1, async_node_2], bus=bus)
    result = await runner.run(sample_state)
    
    # Metrics should be collected
    summary = metrics.get_summary()
    assert summary["graph_executions"] == 1
    assert "async_node_1" in summary["nodes"]
```

**Acceptance Criteria:**
- [ ] All 15+ tests pass
- [ ] AsyncRunner tests: execution, streaming, sync nodes, error handling
- [ ] ConcurrentRunner tests: parallel execution
- [ ] SSE tests: event streaming, encoding
- [ ] WebSocket tests: streaming, pause/resume, cancel
- [ ] Integration test: SynapticBus + streaming
- [ ] No external dependencies except pytest-asyncio

---

## Module Integration

**File to create:** `axis/streaming/__init__.py` (~50 lines)

```python
"""
Axis Streaming Layer - Real-time execution streaming.

Provides:
- AsyncRunner: Async version of Runner with streaming
- ConcurrentRunner: Parallel node execution
- stream_graph_execution: Server-Sent Events streaming
- WebSocketStreamHandler: Bidirectional WebSocket streaming

Example:
    from axis.streaming import AsyncRunner, stream_graph_execution
    
    # Async execution
    async def my_node(state: GraphState) -> GraphState:
        await asyncio.sleep(0.1)
        return state.with_fact(...)
    
    runner = AsyncRunner(nodes=[my_node])
    
    # Stream updates
    async for state in runner.stream(initial_state):
        print(f"Progress: {len(state.events)} events")
    
    # Or SSE streaming
    async for event in stream_graph_execution(runner, initial_state):
        # Send to HTTP client
        yield event.encode()
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
```

---

## Common Guidelines

1. **Async/await:** All streaming code uses async/await
2. **Immutability:** GraphState never mutated
3. **Stdlib only:** Core uses asyncio (no external deps)
4. **Production-ready:** Real SSE/WebSocket protocols
5. **Type hints:** Full type annotations
6. **Documentation:** Docstrings with examples
7. **Testing:** Minimum 15 tests with pytest-asyncio

---

## Deliverables

**Agent 1 (AsyncRunner):**
- `axis/streaming/async_runner.py` (~300 lines)
- 5 tests in `test_streaming.py`

**Agent 2 (SSE):**
- `axis/streaming/event_stream.py` (~200 lines)
- 3 tests in `test_streaming.py`

**Agent 3 (WebSocket):**
- `axis/streaming/websocket.py` (~200 lines)
- 6 tests in `test_streaming.py`

**Agent 4 (Integration):**
- `axis/streaming/__init__.py` (~50 lines)
- `tests/test_streaming.py` complete (~200 lines)
- Integration tests (2+)

**Integration:**
```python
# Verify all exports
from axis.streaming import (
    AsyncRunner, ConcurrentRunner,
    stream_graph_execution, ServerSentEvent,
    WebSocketStreamHandler, WebSocketMessage,
)
```

**Timeline:** Each agent works in parallel. Total: 1-2 days for all 4.

---

## Validation Checklist

After all agents complete:

- [ ] All existing tests still pass (60+ passing, 5 skipped)
- [ ] 15+ new streaming tests pass
- [ ] AsyncRunner executes async nodes
- [ ] stream() yields after each node
- [ ] ConcurrentRunner runs in parallel
- [ ] SSE format valid
- [ ] WebSocket pause/resume/cancel works
- [ ] No regressions in core
- [ ] Documentation updated
- [ ] Copilot instructions note Week 7-8 complete

**Success metric:**
```bash
cd /home/caravaggio/axis
python3 -m pytest tests/ -v
# Expected: 75+ tests passing (60 existing + 15 streaming)
```

---

## Architecture Notes

**Design Philosophy:**
- **Async-first:** Native async/await support
- **Streaming:** Real-time updates via generators
- **Standards-compliant:** Real SSE and WebSocket protocols
- **Backpressure:** Streaming respects async iteration
- **Cancellation:** Graceful shutdown support

**NOT Included (intentional):**
- HTTP server implementation (use FastAPI/aiohttp)
- WebSocket library (use websockets package for production)
- Load balancing (use external tools)
- Authentication (add via middleware)

**Production Upgrade Path:**
```bash
# For production HTTP/WebSocket:
pip install fastapi uvicorn  # For SSE endpoints
pip install websockets        # For WebSocket support
pip install aiohttp          # Alternative HTTP framework
```

**FastAPI Integration Example:**
```python
from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.get("/stream")
async def stream_endpoint():
    runner = AsyncRunner(nodes=[...])
    state = GraphState(trace_id="...")
    
    async def event_generator():
        async for event in stream_graph_execution(runner, state):
            yield event.encode()
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    handler = WebSocketStreamHandler()
    runner = AsyncRunner(nodes=[...])
    state = GraphState(trace_id="...")
    
    async for message in handler.stream(runner, state):
        await websocket.send_text(message.to_json())
```

**Vitruvyan Context:**
- 8 months production with real-time streaming
- SSE critical for UI responsiveness
- WebSocket for interactive debugging
- Async execution 3x faster for I/O-bound tasks

---
