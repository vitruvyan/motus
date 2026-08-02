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
        # Determine log level based on event type (an axis.events.EventType
        # value, e.g. "error" — not an ad-hoc string).
        level = "INFO"
        if event_type == "error":
            level = "ERROR"
        elif event_type in ("node_skipped", "node_retried"):
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
        if event_type == "graph_start":
            return "Graph execution started"
        elif event_type == "graph_end":
            return "Graph execution completed"
        elif event_type == "node_started":
            node_name = kwargs.get("node_name", "unknown")
            return f"Executing node: {node_name}"
        elif event_type == "node_completed":
            node_name = kwargs.get("node_name", "unknown")
            return f"Node completed: {node_name}"
        elif event_type == "error":
            node_name = kwargs.get("node_name", "unknown")
            error = kwargs.get("error", "unknown error")
            return f"Node failed: {node_name} - {error}"
        elif event_type == "node_skipped":
            node_name = kwargs.get("node_name", "unknown")
            return f"Node skipped: {node_name}"
        elif event_type == "node_retried":
            node_name = kwargs.get("node_name", "unknown")
            return f"Node retrying: {node_name}"
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