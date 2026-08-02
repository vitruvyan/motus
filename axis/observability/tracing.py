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
        self.skipped_count: int = 0

    def observe(self, event_type: str, state, **kwargs):
        """
        SynapticBus observer callback.

        Creates spans for (axis.events.EventType values):
        - graph_start → graph span
        - node_started → node span (child of graph)
        - node_completed → end node span
        - node_skipped → tally (the span was already closed by ERROR,
          which EXPLORATION always emits before NODE_SKIPPED)
        - node_retried → bump a retry count on the still-open node span
        - error → mark span as error
        """
        if event_type == "graph_start":
            self._on_graph_start(state)
        elif event_type == "graph_end":
            self._on_graph_end(state)
        elif event_type == "node_started":
            self._on_pre_node(state, **kwargs)
        elif event_type == "node_completed":
            self._on_post_node(state, **kwargs)
        elif event_type == "node_skipped":
            self._on_node_skipped(state, **kwargs)
        elif event_type == "node_retried":
            self._on_node_retried(state, **kwargs)
        elif event_type == "error":
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
            span.end()
            self.spans.append(span)
            del self._active_spans[node_name]

    def _on_node_skipped(self, state, node_name: Optional[str] = None, **kwargs):
        """EXPLORATION always emits ERROR before NODE_SKIPPED, so the span
        is already closed and appended by _on_error by the time this
        fires — nothing left to close, just count it."""
        self.skipped_count += 1

    def _on_node_retried(self, state, node_name: Optional[str] = None, **kwargs):
        """Bump a retry counter on the node's still-open span, if any."""
        if node_name and node_name in self._active_spans:
            span = self._active_spans[node_name]
            span.attributes["retries"] = span.attributes.get("retries", 0) + 1

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
        self.skipped_count = 0