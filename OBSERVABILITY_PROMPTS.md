# Axis Phase 2.3 Week 5-6 - Observability Prompts

**Context:** You are implementing observability for Axis, a minimal cognitive graph kernel.  
**Architecture:** Immutable GraphState, frozen dataclasses, passive observation via SynapticBus.  
**Goal:** Enable production monitoring with metrics, structured logging, and distributed tracing.

---

## PROMPT 1: Prometheus Metrics

**Task:** Implement Prometheus metrics collection via SynapticBus observer.

**File to create:** `axis/observability/metrics.py` (~200 lines)

**Pattern:** Observer that listens to SynapticBus events and exposes Prometheus metrics.

**Requirements:**

1. **Core metrics class:**
```python
from typing import Dict, Optional
from collections import defaultdict
from datetime import datetime
import time
import logging

logger = logging.getLogger(__name__)

class PrometheusMetrics:
    """
    Prometheus metrics collector for Axis.
    
    Collects metrics by observing SynapticBus events:
    - axis_node_duration_seconds: Histogram of node execution times
    - axis_node_errors_total: Counter of node failures
    - axis_graph_executions_total: Counter of graph executions
    - axis_state_size_bytes: Gauge of GraphState size
    
    Example:
        from axis.observability import PrometheusMetrics
        from axis.synaptic_bus import SynapticBus
        
        bus = SynapticBus()
        metrics = PrometheusMetrics()
        bus.attach(metrics)
        
        # Metrics auto-collected during execution
        # Access via metrics.get_metrics()
    """
    
    def __init__(self, namespace: str = "axis"):
        self.namespace = namespace
        
        # Metric storage (in-memory, simple implementation)
        self.node_durations: Dict[str, list] = defaultdict(list)
        self.node_errors: Dict[str, int] = defaultdict(int)
        self.graph_executions: int = 0
        self.state_sizes: list = []
        
        # Timing tracking
        self._node_start_times: Dict[str, float] = {}
    
    def observe(self, event_type: str, state, **kwargs):
        """
        SynapticBus observer callback.
        
        Called on:
        - PRE_NODE: Record start time
        - POST_NODE: Record duration
        - ERROR: Record failure
        - GRAPH_START: Increment execution counter
        """
        if event_type == "PRE_NODE":
            self._on_pre_node(state, **kwargs)
        elif event_type == "POST_NODE":
            self._on_post_node(state, **kwargs)
        elif event_type == "ERROR":
            self._on_error(state, **kwargs)
        elif event_type == "GRAPH_START":
            self._on_graph_start(state, **kwargs)
    
    def _on_pre_node(self, state, node_name: Optional[str] = None, **kwargs):
        """Record node start time."""
        if node_name:
            self._node_start_times[node_name] = time.time()
    
    def _on_post_node(self, state, node_name: Optional[str] = None, **kwargs):
        """Record node duration."""
        if node_name and node_name in self._node_start_times:
            duration = time.time() - self._node_start_times[node_name]
            self.node_durations[node_name].append(duration)
            del self._node_start_times[node_name]
    
    def _on_error(self, state, node_name: Optional[str] = None, **kwargs):
        """Record node error."""
        if node_name:
            self.node_errors[node_name] += 1
    
    def _on_graph_start(self, state, **kwargs):
        """Record graph execution."""
        self.graph_executions += 1
        
        # Track state size
        import sys
        size = sys.getsizeof(state.to_dict())
        self.state_sizes.append(size)
    
    def get_metrics(self) -> str:
        """
        Export metrics in Prometheus text format.
        
        Returns:
            Metrics formatted as Prometheus exposition format
        """
        lines = []
        
        # Node duration histogram (simplified, no buckets)
        lines.append("# HELP axis_node_duration_seconds Node execution duration")
        lines.append("# TYPE axis_node_duration_seconds histogram")
        for node_name, durations in self.node_durations.items():
            if durations:
                avg_duration = sum(durations) / len(durations)
                lines.append(
                    f'axis_node_duration_seconds{{node="{node_name}"}} {avg_duration:.6f}'
                )
        
        # Node errors counter
        lines.append("# HELP axis_node_errors_total Node execution errors")
        lines.append("# TYPE axis_node_errors_total counter")
        for node_name, count in self.node_errors.items():
            lines.append(f'axis_node_errors_total{{node="{node_name}"}} {count}')
        
        # Graph executions counter
        lines.append("# HELP axis_graph_executions_total Total graph executions")
        lines.append("# TYPE axis_graph_executions_total counter")
        lines.append(f"axis_graph_executions_total {self.graph_executions}")
        
        # State size gauge
        if self.state_sizes:
            avg_size = sum(self.state_sizes) / len(self.state_sizes)
            lines.append("# HELP axis_state_size_bytes Average GraphState size")
            lines.append("# TYPE axis_state_size_bytes gauge")
            lines.append(f"axis_state_size_bytes {avg_size:.0f}")
        
        return "\n".join(lines) + "\n"
    
    def get_summary(self) -> dict:
        """
        Get metrics summary as dictionary.
        
        Returns:
            Dict with aggregated metrics
        """
        summary = {
            "graph_executions": self.graph_executions,
            "nodes": {},
        }
        
        for node_name, durations in self.node_durations.items():
            if durations:
                summary["nodes"][node_name] = {
                    "executions": len(durations),
                    "avg_duration": sum(durations) / len(durations),
                    "min_duration": min(durations),
                    "max_duration": max(durations),
                    "errors": self.node_errors.get(node_name, 0),
                }
        
        return summary
    
    def reset(self):
        """Reset all metrics (useful for testing)."""
        self.node_durations.clear()
        self.node_errors.clear()
        self.graph_executions = 0
        self.state_sizes.clear()
        self._node_start_times.clear()
```

2. **HTTP endpoint (optional, for Prometheus scraping):**
```python
from http.server import HTTPServer, BaseHTTPRequestHandler

class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler for /metrics endpoint."""
    
    metrics_collector: Optional['PrometheusMetrics'] = None
    
    def do_GET(self):
        if self.path == "/metrics":
            metrics = self.metrics_collector.get_metrics()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(metrics.encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress HTTP logs
        pass

def start_metrics_server(
    metrics: PrometheusMetrics,
    port: int = 9090,
) -> HTTPServer:
    """
    Start HTTP server for Prometheus scraping.
    
    Args:
        metrics: PrometheusMetrics instance
        port: Port to listen on (default: 9090)
    
    Returns:
        HTTPServer instance (call .serve_forever() to run)
    
    Example:
        metrics = PrometheusMetrics()
        server = start_metrics_server(metrics, port=9090)
        # In background thread:
        server.serve_forever()
    """
    MetricsHandler.metrics_collector = metrics
    server = HTTPServer(("0.0.0.0", port), MetricsHandler)
    logger.info(f"Metrics server listening on http://0.0.0.0:{port}/metrics")
    return server
```

**Key principles:**
- Passive observation via SynapticBus (no GraphState mutation)
- In-memory metrics (simple, no external deps)
- Prometheus exposition format
- Optional HTTP endpoint for scraping

**Acceptance Criteria:**
- [ ] Observes SynapticBus events (PRE_NODE, POST_NODE, ERROR, GRAPH_START)
- [ ] Records node durations accurately
- [ ] Counts node errors
- [ ] Counts graph executions
- [ ] Tracks state size
- [ ] get_metrics() returns Prometheus format
- [ ] get_summary() returns dict for programmatic access
- [ ] Optional HTTP server for scraping
- [ ] No external dependencies (stdlib only)

---

## PROMPT 2: Structured Logging

**Task:** Implement structured JSON logging for Axis execution traces.

**File to create:** `axis/observability/logging.py` (~150 lines)

**Pattern:** Observer that emits structured JSON logs for all events.

**Requirements:**

1. **Structured logger class:**
```python
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime
import sys

class StructuredLogger:
    """
    Structured JSON logger for Axis.
    
    Emits JSON logs for all SynapticBus events with:
    - timestamp: ISO 8601 format
    - level: INFO, WARNING, ERROR
    - trace_id: GraphState trace ID
    - event_type: SynapticBus event type
    - node_name: Node being executed (if applicable)
    - message: Human-readable message
    - context: Additional metadata
    
    Example:
        from axis.observability import StructuredLogger
        from axis.synaptic_bus import SynapticBus
        
        bus = SynapticBus()
        logger = StructuredLogger(output=sys.stdout)
        bus.attach(logger)
        
        # JSON logs auto-emitted during execution
    """
    
    def __init__(
        self,
        output=sys.stdout,
        min_level: str = "INFO",
        include_state: bool = False,
    ):
        """
        Args:
            output: File-like object for log output (default: stdout)
            min_level: Minimum log level (DEBUG, INFO, WARNING, ERROR)
            include_state: Include full GraphState in logs (default: False)
        """
        self.output = output
        self.min_level = min_level
        self.include_state = include_state
        
        # Level hierarchy
        self._levels = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
        self._min_level_value = self._levels.get(min_level, 1)
    
    def observe(self, event_type: str, state, **kwargs):
        """
        SynapticBus observer callback.
        
        Emits structured JSON log for each event.
        """
        log_entry = self._create_log_entry(event_type, state, **kwargs)
        
        # Filter by level
        level_value = self._levels.get(log_entry["level"], 1)
        if level_value >= self._min_level_value:
            self._emit(log_entry)
    
    def _create_log_entry(
        self,
        event_type: str,
        state,
        **kwargs
    ) -> Dict[str, Any]:
        """Create structured log entry."""
        # Determine log level based on event type
        level = "INFO"
        if event_type == "ERROR":
            level = "ERROR"
        elif event_type == "REJECTION":
            level = "WARNING"
        
        # Base log entry
        entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "trace_id": state.trace_id,
            "event_type": event_type,
            "message": self._create_message(event_type, **kwargs),
        }
        
        # Add node name if present
        if "node_name" in kwargs:
            entry["node_name"] = kwargs["node_name"]
        
        # Add error details if present
        if "error" in kwargs:
            entry["error"] = {
                "type": type(kwargs["error"]).__name__,
                "message": str(kwargs["error"]),
            }
        
        # Add decision/rejection details
        if "decision" in kwargs:
            entry["decision"] = kwargs["decision"]
        if "rejection" in kwargs:
            entry["rejection"] = kwargs["rejection"]
        
        # Optionally include full state
        if self.include_state:
            entry["state"] = state.to_dict()
        else:
            # Include summary
            entry["state_summary"] = {
                "facts_count": len(state.facts),
                "decisions_count": len(state.decisions),
                "rejections_count": len(state.rejections),
                "events_count": len(state.events),
            }
        
        return entry
    
    def _create_message(self, event_type: str, **kwargs) -> str:
        """Create human-readable message."""
        if event_type == "GRAPH_START":
            return "Graph execution started"
        elif event_type == "GRAPH_END":
            return "Graph execution completed"
        elif event_type == "PRE_NODE":
            node_name = kwargs.get("node_name", "unknown")
            return f"Executing node: {node_name}"
        elif event_type == "POST_NODE":
            node_name = kwargs.get("node_name", "unknown")
            return f"Node completed: {node_name}"
        elif event_type == "ERROR":
            node_name = kwargs.get("node_name", "unknown")
            error = kwargs.get("error", "unknown error")
            return f"Node failed: {node_name} - {error}"
        elif event_type == "DECISION":
            return "Decision recorded"
        elif event_type == "REJECTION":
            return "Rejection recorded"
        else:
            return f"Event: {event_type}"
    
    def _emit(self, log_entry: Dict[str, Any]):
        """Emit JSON log to output."""
        try:
            json_line = json.dumps(log_entry, default=str)
            self.output.write(json_line + "\n")
            self.output.flush()
        except Exception as e:
            # Fallback to stderr if output fails
            print(f"Failed to emit log: {e}", file=sys.stderr)
```

2. **File logger wrapper:**
```python
class FileLogger(StructuredLogger):
    """StructuredLogger that writes to a file."""
    
    def __init__(
        self,
        filepath: str,
        min_level: str = "INFO",
        include_state: bool = False,
    ):
        """
        Args:
            filepath: Path to log file
            min_level: Minimum log level
            include_state: Include full GraphState in logs
        """
        self.filepath = filepath
        self.file = open(filepath, "a")
        super().__init__(
            output=self.file,
            min_level=min_level,
            include_state=include_state,
        )
    
    def close(self):
        """Close log file."""
        if self.file:
            self.file.close()
    
    def __del__(self):
        self.close()
```

3. **Log parsing utilities:**
```python
def parse_log_file(filepath: str) -> list[Dict[str, Any]]:
    """
    Parse JSON log file into list of entries.
    
    Args:
        filepath: Path to log file
    
    Returns:
        List of log entries as dicts
    """
    entries = []
    with open(filepath, "r") as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue
    return entries

def filter_logs(
    entries: list[Dict[str, Any]],
    trace_id: Optional[str] = None,
    level: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """
    Filter log entries by criteria.
    
    Args:
        entries: List of log entries
        trace_id: Filter by trace ID
        level: Filter by log level
        event_type: Filter by event type
    
    Returns:
        Filtered list of entries
    """
    filtered = entries
    
    if trace_id:
        filtered = [e for e in filtered if e.get("trace_id") == trace_id]
    if level:
        filtered = [e for e in filtered if e.get("level") == level]
    if event_type:
        filtered = [e for e in filtered if e.get("event_type") == event_type]
    
    return filtered
```

**Key principles:**
- Structured JSON (machine-readable)
- ISO 8601 timestamps
- Trace ID correlation
- Human-readable messages
- Configurable verbosity

**Acceptance Criteria:**
- [ ] Emits JSON logs for all SynapticBus events
- [ ] Includes timestamp, level, trace_id, event_type, message
- [ ] Configurable output (stdout, file)
- [ ] Configurable min_level filtering
- [ ] Optional full state inclusion
- [ ] Log parsing utilities
- [ ] No external dependencies (stdlib only)

---

## PROMPT 3: OpenTelemetry Tracing

**Task:** Implement distributed tracing with OpenTelemetry spans.

**File to create:** `axis/observability/tracing.py` (~100 lines)

**Pattern:** Observer that creates OpenTelemetry spans for node execution.

**Requirements:**

1. **Span tracer class (no external deps version):**
```python
from typing import Optional, Dict, Any
from datetime import datetime
import time

class Span:
    """
    Simple span implementation (OpenTelemetry-compatible structure).
    
    Note: This is a minimal implementation without OpenTelemetry SDK.
    For production, use opentelemetry-api and opentelemetry-sdk packages.
    """
    
    def __init__(
        self,
        name: str,
        trace_id: str,
        parent_span_id: Optional[str] = None,
    ):
        self.name = name
        self.trace_id = trace_id
        self.span_id = self._generate_span_id()
        self.parent_span_id = parent_span_id
        
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.attributes: Dict[str, Any] = {}
        self.status: str = "OK"
        self.error: Optional[str] = None
    
    def start(self):
        """Start span timing."""
        self.start_time = time.time()
    
    def end(self):
        """End span timing."""
        self.end_time = time.time()
    
    def set_attribute(self, key: str, value: Any):
        """Set span attribute."""
        self.attributes[key] = value
    
    def set_error(self, error: Exception):
        """Mark span as error."""
        self.status = "ERROR"
        self.error = str(error)
        self.attributes["error.type"] = type(error).__name__
        self.attributes["error.message"] = str(error)
    
    def to_dict(self) -> dict:
        """Export span as dict (JSON-serializable)."""
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": (self.end_time - self.start_time) if self.end_time else None,
            "attributes": self.attributes,
            "status": self.status,
            "error": self.error,
        }
    
    @staticmethod
    def _generate_span_id() -> str:
        """Generate unique span ID."""
        import random
        return f"{random.randint(0, 2**64-1):016x}"

class SpanTracer:
    """
    Span tracer for Axis execution.
    
    Creates one span per node execution, nested under graph span.
    
    Example:
        from axis.observability import SpanTracer
        from axis.synaptic_bus import SynapticBus
        
        bus = SynapticBus()
        tracer = SpanTracer()
        bus.attach(tracer)
        
        # Spans auto-created during execution
        # Access via tracer.get_spans()
    """
    
    def __init__(self):
        self.spans: list[Span] = []
        self._active_spans: Dict[str, Span] = {}
        self._graph_span: Optional[Span] = None
    
    def observe(self, event_type: str, state, **kwargs):
        """
        SynapticBus observer callback.
        
        Creates spans for:
        - GRAPH_START → graph span
        - PRE_NODE → node span (child of graph)
        - POST_NODE → end node span
        - ERROR → mark span as error
        """
        if event_type == "GRAPH_START":
            self._on_graph_start(state)
        elif event_type == "GRAPH_END":
            self._on_graph_end(state)
        elif event_type == "PRE_NODE":
            self._on_pre_node(state, **kwargs)
        elif event_type == "POST_NODE":
            self._on_post_node(state, **kwargs)
        elif event_type == "ERROR":
            self._on_error(state, **kwargs)
    
    def _on_graph_start(self, state):
        """Create graph-level span."""
        self._graph_span = Span(
            name="axis.graph.execute",
            trace_id=state.trace_id,
        )
        self._graph_span.start()
        self._graph_span.set_attribute("trace_id", state.trace_id)
        if state.intent:
            self._graph_span.set_attribute("intent", state.intent)
    
    def _on_graph_end(self, state):
        """End graph-level span."""
        if self._graph_span:
            self._graph_span.end()
            self._graph_span.set_attribute("facts_count", len(state.facts))
            self._graph_span.set_attribute("decisions_count", len(state.decisions))
            self.spans.append(self._graph_span)
            self._graph_span = None
    
    def _on_pre_node(self, state, node_name: Optional[str] = None, **kwargs):
        """Create node span."""
        if node_name:
            span = Span(
                name=f"axis.node.{node_name}",
                trace_id=state.trace_id,
                parent_span_id=self._graph_span.span_id if self._graph_span else None,
            )
            span.start()
            span.set_attribute("node_name", node_name)
            self._active_spans[node_name] = span
    
    def _on_post_node(self, state, node_name: Optional[str] = None, **kwargs):
        """End node span."""
        if node_name and node_name in self._active_spans:
            span = self._active_spans[node_name]
            span.end()
            self.spans.append(span)
            del self._active_spans[node_name]
    
    def _on_error(self, state, node_name: Optional[str] = None, error=None, **kwargs):
        """Mark span as error."""
        if node_name and node_name in self._active_spans:
            span = self._active_spans[node_name]
            if error:
                span.set_error(error)
    
    def get_spans(self) -> list[Span]:
        """Get all completed spans."""
        return self.spans
    
    def export_spans(self) -> list[dict]:
        """Export spans as JSON-serializable dicts."""
        return [span.to_dict() for span in self.spans]
    
    def reset(self):
        """Reset tracer (useful for testing)."""
        self.spans.clear()
        self._active_spans.clear()
        self._graph_span = None
```

**Note on OpenTelemetry:**
This is a minimal implementation without external dependencies. For production use with real OpenTelemetry:

```python
# Production version with opentelemetry-sdk:
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

# Setup
provider = TracerProvider()
processor = SimpleSpanProcessor(ConsoleSpanExporter())
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)

# In observer:
with tracer.start_as_current_span(f"axis.node.{node_name}") as span:
    span.set_attribute("trace_id", state.trace_id)
    # ...
```

**Key principles:**
- One span per node execution
- Graph span as parent
- Nested spans for hierarchy
- Attributes for context
- Error status on failure

**Acceptance Criteria:**
- [ ] Creates graph span on GRAPH_START
- [ ] Creates node spans on PRE_NODE
- [ ] Ends spans on POST_NODE/GRAPH_END
- [ ] Marks spans as ERROR on failure
- [ ] Attributes include trace_id, node_name
- [ ] export_spans() returns JSON-serializable list
- [ ] No external dependencies (minimal implementation)
- [ ] Comment about OpenTelemetry SDK for production

---

## PROMPT 4: Tests & Integration

**Task:** Comprehensive test suite for observability layer.

**File to create:** `tests/test_observability.py` (~150 lines)

**Requirements:**

1. **Test fixtures:**
```python
import pytest
from axis.state import GraphState, Fact, Decision
from axis.synaptic_bus import SynapticBus
from axis.runner import Runner
from axis.observability.metrics import PrometheusMetrics
from axis.observability.logging import StructuredLogger
from axis.observability.tracing import SpanTracer
from datetime import datetime
import io
import json

@pytest.fixture
def sample_state():
    return GraphState(trace_id="test-obs-123")

def simple_node(state: GraphState) -> GraphState:
    """Simple node for testing."""
    fact = Fact("test", "value", "node", datetime.now())
    return state.with_fact(fact)

def failing_node(state: GraphState) -> GraphState:
    """Node that raises error."""
    raise ValueError("Test error")
```

2. **Metrics tests:**
```python
def test_metrics_records_node_duration(sample_state):
    """Test PrometheusMetrics records node execution time."""
    bus = SynapticBus()
    metrics = PrometheusMetrics()
    bus.attach(metrics)
    
    runner = Runner(nodes=[simple_node], bus=bus)
    result = runner.run(sample_state)
    
    summary = metrics.get_summary()
    assert summary["graph_executions"] == 1
    assert "simple_node" in summary["nodes"]
    assert summary["nodes"]["simple_node"]["executions"] == 1
    assert summary["nodes"]["simple_node"]["avg_duration"] > 0

def test_metrics_counts_errors(sample_state):
    """Test PrometheusMetrics counts node errors."""
    from axis.policy import Policy
    
    bus = SynapticBus()
    metrics = PrometheusMetrics()
    bus.attach(metrics)
    
    runner = Runner(nodes=[failing_node], bus=bus, policy=Policy.EXPLORATION)
    result = runner.run(sample_state)
    
    summary = metrics.get_summary()
    assert "failing_node" in summary["nodes"]
    assert summary["nodes"]["failing_node"]["errors"] == 1

def test_metrics_prometheus_format():
    """Test PrometheusMetrics exports Prometheus format."""
    metrics = PrometheusMetrics()
    metrics.graph_executions = 5
    metrics.node_durations["test_node"] = [0.1, 0.2, 0.3]
    
    output = metrics.get_metrics()
    
    assert "# HELP axis_node_duration_seconds" in output
    assert "# TYPE axis_node_duration_seconds histogram" in output
    assert "axis_graph_executions_total 5" in output
    assert "test_node" in output
```

3. **Logging tests:**
```python
def test_structured_logger_emits_json(sample_state):
    """Test StructuredLogger emits valid JSON."""
    output = io.StringIO()
    logger = StructuredLogger(output=output)
    
    bus = SynapticBus()
    bus.attach(logger)
    
    runner = Runner(nodes=[simple_node], bus=bus)
    result = runner.run(sample_state)
    
    # Parse JSON logs
    logs = output.getvalue().strip().split("\n")
    assert len(logs) > 0
    
    for log_line in logs:
        entry = json.loads(log_line)
        assert "timestamp" in entry
        assert "level" in entry
        assert "trace_id" in entry
        assert entry["trace_id"] == "test-obs-123"

def test_structured_logger_filters_by_level(sample_state):
    """Test StructuredLogger filters by min_level."""
    output = io.StringIO()
    logger = StructuredLogger(output=output, min_level="ERROR")
    
    bus = SynapticBus()
    bus.attach(logger)
    
    runner = Runner(nodes=[simple_node], bus=bus)
    result = runner.run(sample_state)
    
    # Only ERROR logs should appear (none in this case)
    logs = output.getvalue().strip()
    # Should have fewer logs than with INFO level
    assert len(logs) < 100  # Heuristic

def test_structured_logger_includes_error_details(sample_state):
    """Test StructuredLogger includes error details."""
    from axis.policy import Policy
    
    output = io.StringIO()
    logger = StructuredLogger(output=output)
    
    bus = SynapticBus()
    bus.attach(logger)
    
    runner = Runner(nodes=[failing_node], bus=bus, policy=Policy.EXPLORATION)
    result = runner.run(sample_state)
    
    logs = output.getvalue().strip().split("\n")
    error_logs = [json.loads(log) for log in logs if json.loads(log).get("level") == "ERROR"]
    
    assert len(error_logs) > 0
    assert error_logs[0]["error"]["type"] == "ValueError"
    assert "Test error" in error_logs[0]["error"]["message"]
```

4. **Tracing tests:**
```python
def test_span_tracer_creates_graph_span(sample_state):
    """Test SpanTracer creates graph-level span."""
    bus = SynapticBus()
    tracer = SpanTracer()
    bus.attach(tracer)
    
    runner = Runner(nodes=[simple_node], bus=bus)
    result = runner.run(sample_state)
    
    spans = tracer.export_spans()
    assert len(spans) >= 1
    
    graph_span = [s for s in spans if s["name"] == "axis.graph.execute"][0]
    assert graph_span["trace_id"] == "test-obs-123"
    assert graph_span["status"] == "OK"
    assert graph_span["duration"] > 0

def test_span_tracer_creates_node_spans(sample_state):
    """Test SpanTracer creates node-level spans."""
    bus = SynapticBus()
    tracer = SpanTracer()
    bus.attach(tracer)
    
    runner = Runner(nodes=[simple_node], bus=bus)
    result = runner.run(sample_state)
    
    spans = tracer.export_spans()
    node_spans = [s for s in spans if "node" in s["name"]]
    
    assert len(node_spans) >= 1
    assert node_spans[0]["attributes"]["node_name"] == "simple_node"

def test_span_tracer_marks_errors(sample_state):
    """Test SpanTracer marks failed spans as ERROR."""
    from axis.policy import Policy
    
    bus = SynapticBus()
    tracer = SpanTracer()
    bus.attach(tracer)
    
    runner = Runner(nodes=[failing_node], bus=bus, policy=Policy.EXPLORATION)
    result = runner.run(sample_state)
    
    spans = tracer.export_spans()
    error_spans = [s for s in spans if s["status"] == "ERROR"]
    
    assert len(error_spans) > 0
    assert error_spans[0]["attributes"]["error.type"] == "ValueError"
```

5. **Integration test:**
```python
def test_all_observers_together(sample_state):
    """Test all observability layers work together."""
    bus = SynapticBus()
    
    metrics = PrometheusMetrics()
    logger = StructuredLogger(output=io.StringIO())
    tracer = SpanTracer()
    
    bus.attach(metrics)
    bus.attach(logger)
    bus.attach(tracer)
    
    runner = Runner(nodes=[simple_node], bus=bus)
    result = runner.run(sample_state)
    
    # All observers should have data
    assert metrics.get_summary()["graph_executions"] == 1
    assert len(tracer.export_spans()) >= 1
    # Logger output checked separately
```

**Acceptance Criteria:**
- [ ] All 12+ tests pass
- [ ] Metrics tests: duration, errors, prometheus format
- [ ] Logging tests: JSON format, filtering, error details
- [ ] Tracing tests: graph span, node spans, error marking
- [ ] Integration test: all observers together
- [ ] No external dependencies (stdlib only)

---

## Module Integration

**File to create:** `axis/observability/__init__.py` (~50 lines)

```python
"""
Axis Observability Layer - Production monitoring.

Provides:
- PrometheusMetrics: Metrics collection via SynapticBus
- StructuredLogger: JSON logging for trace correlation
- SpanTracer: Distributed tracing spans

Example:
    from axis.observability import PrometheusMetrics, StructuredLogger, SpanTracer
    from axis.synaptic_bus import SynapticBus
    
    bus = SynapticBus()
    
    # Attach observers
    bus.attach(PrometheusMetrics())
    bus.attach(StructuredLogger())
    bus.attach(SpanTracer())
    
    # Execute with observability
    runner = Runner(nodes=[...], bus=bus)
    result = runner.run(state)
"""

from axis.observability.metrics import (
    PrometheusMetrics,
    start_metrics_server,
)

from axis.observability.logging import (
    StructuredLogger,
    FileLogger,
    parse_log_file,
    filter_logs,
)

from axis.observability.tracing import (
    SpanTracer,
    Span,
)

__all__ = [
    # Metrics
    "PrometheusMetrics",
    "start_metrics_server",
    # Logging
    "StructuredLogger",
    "FileLogger",
    "parse_log_file",
    "filter_logs",
    # Tracing
    "SpanTracer",
    "Span",
]
```

---

## Common Guidelines

1. **Passive observation:** Never modify GraphState
2. **SynapticBus integration:** All observers attach to bus
3. **Stdlib only:** No external dependencies (minimal implementations)
4. **Production-ready:** Real Prometheus format, JSON logs, OTel-compatible spans
5. **Type hints:** Full type annotations
6. **Documentation:** Docstrings with examples
7. **Testing:** Minimum 12 tests for full coverage

---

## Deliverables

**Agent 1 (Metrics):**
- `axis/observability/metrics.py` (~200 lines)
- 4 tests in `test_observability.py`

**Agent 2 (Logging):**
- `axis/observability/logging.py` (~150 lines)
- 4 tests in `test_observability.py`

**Agent 3 (Tracing):**
- `axis/observability/tracing.py` (~100 lines)
- 4 tests in `test_observability.py`

**Agent 4 (Integration):**
- `axis/observability/__init__.py` (~50 lines)
- `tests/test_observability.py` complete (~150 lines)
- Integration tests (1+)

**Integration:**
```python
# Verify all exports
from axis.observability import (
    PrometheusMetrics, StructuredLogger, SpanTracer,
    start_metrics_server, FileLogger,
)
```

**Timeline:** Each agent works in parallel. Total: 1-2 days for all 4.

---

## Validation Checklist

After all agents complete:

- [ ] All existing tests still pass (48 passing, 5 skipped)
- [ ] 12+ new observability tests pass
- [ ] PrometheusMetrics exports valid format
- [ ] StructuredLogger emits valid JSON
- [ ] SpanTracer creates nested spans
- [ ] All observers work together
- [ ] No regressions in core
- [ ] Documentation updated
- [ ] Copilot instructions note Week 5-6 complete

**Success metric:**
```bash
cd /home/caravaggio/axis
python3 -m pytest tests/ -v
# Expected: 60+ tests passing (48 existing + 12 observability)
```

---

## Architecture Notes

**Design Philosophy:**
- **Passive observation:** Zero impact on GraphState immutability
- **SynapticBus native:** Built-in integration point
- **Production formats:** Real Prometheus, JSON logs, OTel spans
- **Minimal deps:** Stdlib-only implementations (production can upgrade)

**NOT Included (intentional):**
- OpenTelemetry SDK (add via pip for production)
- Prometheus client library (add via pip for production)
- Log aggregation (use external tools: ELK, Datadog)
- Metrics storage (Prometheus handles this)

**Production Upgrade Path:**
```bash
# For production, add:
pip install prometheus-client  # Real Prometheus metrics
pip install opentelemetry-api opentelemetry-sdk  # Real tracing
```

**Vitruvyan Context:**
- 8 months production experience with observability
- Prometheus metrics essential for SLA monitoring
- JSON logs critical for debugging (trace_id correlation)
- Tracing helped debug multi-node workflows

---
