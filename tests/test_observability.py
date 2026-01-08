import pytest
from axis.state import GraphState, Fact, Decision
from axis.runner import Runner
from axis.observability.metrics import PrometheusMetrics
from axis.observability.logging import StructuredLogger
from axis.observability.tracing import SpanTracer
from datetime import datetime
import io
import json

@pytest.fixture
def sample_state():
    return GraphState(trace_id="test-obs-123", intent=None, facts=(), decisions=(), rejections=(), events=())

def simple_node(state: GraphState) -> GraphState:
    """Simple node for testing."""
    fact = Fact("test", "value", "node", datetime.now())
    return state.with_fact(fact)

def failing_node(state: GraphState) -> GraphState:
    """Node that raises error."""
    raise ValueError("Test error")

# Metrics tests
def test_metrics_records_node_duration(sample_state):
    """Test PrometheusMetrics records node execution time."""
    metrics = PrometheusMetrics()
    
    runner = Runner(nodes=[simple_node])
    runner.attach(metrics)
    result = runner.run(sample_state)
    
    summary = metrics.get_summary()
    assert summary["graph_executions"] == 1
    assert "simple_node" in summary["nodes"]
    assert summary["nodes"]["simple_node"]["executions"] == 1
    assert summary["nodes"]["simple_node"]["avg_duration"] > 0

def test_metrics_counts_errors(sample_state):
    """Test PrometheusMetrics counts node errors."""
    from axis.policy import Policy
    
    metrics = PrometheusMetrics()
    
    runner = Runner(nodes=[failing_node], policy=Policy.EXPLORATION)
    runner.attach(metrics)
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

# Logging tests
def test_structured_logger_emits_json(sample_state):
    """Test StructuredLogger emits valid JSON."""
    output = io.StringIO()
    logger = StructuredLogger(output=output)
    
    runner = Runner(nodes=[simple_node])
    runner.attach(logger)
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
    
    runner = Runner(nodes=[simple_node])
    runner.attach(logger)
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
    
    runner = Runner(nodes=[failing_node], policy=Policy.EXPLORATION)
    runner.attach(logger)
    result = runner.run(sample_state)
    
    logs = output.getvalue().strip().split("\n")
    error_logs = [json.loads(log) for log in logs if json.loads(log).get("level") == "ERROR"]
    
    assert len(error_logs) > 0
    assert error_logs[0]["error"]["type"] == "ValueError"
    assert "Test error" in error_logs[0]["error"]["message"]

# Tracing tests
def test_span_tracer_creates_graph_span(sample_state):
    """Test SpanTracer creates graph-level span."""
    tracer = SpanTracer()
    
    runner = Runner(nodes=[simple_node])
    runner.attach(tracer)
    result = runner.run(sample_state)
    
    spans = tracer.export_spans()
    assert len(spans) >= 1
    
    graph_span = [s for s in spans if s["name"] == "axis.graph.execute"][0]
    assert graph_span["trace_id"] == "test-obs-123"
    assert graph_span["status"] == "OK"
    assert graph_span["duration"] > 0

def test_span_tracer_creates_node_spans(sample_state):
    """Test SpanTracer creates node-level spans."""
    tracer = SpanTracer()
    
    runner = Runner(nodes=[simple_node])
    runner.attach(tracer)
    result = runner.run(sample_state)
    
    spans = tracer.export_spans()
    node_spans = [s for s in spans if "node" in s["name"]]
    
    assert len(node_spans) >= 1
    assert node_spans[0]["attributes"]["node_name"] == "simple_node"

def test_span_tracer_marks_errors(sample_state):
    """Test SpanTracer marks failed spans as ERROR."""
    from axis.policy import Policy
    
    tracer = SpanTracer()
    
    runner = Runner(nodes=[failing_node], policy=Policy.EXPLORATION)
    runner.attach(tracer)
    result = runner.run(sample_state)
    
    spans = tracer.export_spans()
    error_spans = [s for s in spans if s["status"] == "ERROR"]
    
    assert len(error_spans) > 0
    assert error_spans[0]["attributes"]["error.type"] == "ValueError"

# Integration test
def test_all_observers_together(sample_state):
    """Test all observability layers work together."""
    metrics = PrometheusMetrics()
    logger = StructuredLogger(output=io.StringIO())
    tracer = SpanTracer()
    
    runner = Runner(nodes=[simple_node])
    runner.attach(metrics)
    runner.attach(logger)
    runner.attach(tracer)
    result = runner.run(sample_state)
    
    # All observers should have data
    assert metrics.get_summary()["graph_executions"] == 1
    assert len(tracer.export_spans()) >= 1
    # Logger output checked separately