import pytest
import asyncio
from axis.state import GraphState, Fact
from axis.streaming.async_runner import AsyncRunner, ConcurrentRunner
from axis.streaming.event_stream import stream_graph_execution, ServerSentEvent
from axis.streaming.websocket import WebSocketStreamHandler, WebSocketMessage
from datetime import datetime

@pytest.fixture
def sample_state():
    return GraphState(
        trace_id="test-stream-123",
        intent=None,
        facts=(),
        decisions=(),
        rejections=(),
        events=(),
    )

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