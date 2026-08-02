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
        self.node_skips: Dict[str, int] = defaultdict(int)
        self.node_retries: Dict[str, int] = defaultdict(int)
        self.graph_executions: int = 0
        self.state_sizes: list = []

        # Timing tracking
        self._node_start_times: Dict[str, float] = {}

    def observe(self, event_type: str, state, **kwargs):
        """
        SynapticBus observer callback.

        Called on:
        - node_started: Record start time
        - node_completed: Record duration
        - node_skipped: Count a skipped node (EXPLORATION policy)
        - node_retried: Count a retry
        - error: Record failure
        - graph_start: Increment execution counter

        event_type is an axis.events.EventType value (e.g. "node_started"),
        not an ad-hoc string — one vocabulary for the whole lifecycle.
        """
        if event_type == "node_started":
            self._on_pre_node(state, **kwargs)
        elif event_type == "node_completed":
            self._on_post_node(state, **kwargs)
        elif event_type == "node_skipped":
            self._on_node_skipped(state, **kwargs)
        elif event_type == "node_retried":
            self._on_node_retried(state, **kwargs)
        elif event_type == "error":
            self._on_error(state, **kwargs)
        elif event_type == "graph_start":
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

    def _on_node_skipped(self, state, node_name: Optional[str] = None, **kwargs):
        """Count a node EXPLORATION skipped — invisible to node_errors,
        which counts every failure regardless of policy; this is the
        subset that didn't stop the run."""
        if node_name:
            self.node_skips[node_name] += 1
            self._node_start_times.pop(node_name, None)

    def _on_node_retried(self, state, node_name: Optional[str] = None, **kwargs):
        """Count a retry attempt."""
        if node_name:
            self.node_retries[node_name] += 1

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
        
        # Include all nodes that have durations, errors, skips, or retries
        all_node_names = (
            set(self.node_durations.keys())
            | set(self.node_errors.keys())
            | set(self.node_skips.keys())
            | set(self.node_retries.keys())
        )

        for node_name in all_node_names:
            durations = self.node_durations.get(node_name, [])
            errors = self.node_errors.get(node_name, 0)

            node_summary = {
                "executions": len(durations),
                "errors": errors,
                "skipped": self.node_skips.get(node_name, 0),
                "retries": self.node_retries.get(node_name, 0),
            }
            
            if durations:
                node_summary.update({
                    "avg_duration": sum(durations) / len(durations),
                    "min_duration": min(durations),
                    "max_duration": max(durations),
                })
            
            summary["nodes"][node_name] = node_summary
        
        return summary
    
    def reset(self):
        """Reset all metrics (useful for testing)."""
        self.node_durations.clear()
        self.node_errors.clear()
        self.node_skips.clear()
        self.node_retries.clear()
        self.graph_executions = 0
        self.state_sizes.clear()
        self._node_start_times.clear()


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