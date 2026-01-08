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
