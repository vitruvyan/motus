from typing import Sequence, Optional, Callable, AsyncIterator
from axis.state import GraphState
from axis.events import Event, EventType
from axis.node import Node
from axis.policy import Policy
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
            self.bus.observe("GRAPH_START", state)
        
        current_state = state
        
        for node in self.nodes:
            try:
                node_name = node.__name__
                
                if self.bus:
                    self.bus.observe("PRE_NODE", current_state, node_name=node_name)
                
                # Execute node (async or sync)
                if asyncio.iscoroutinefunction(node):
                    next_state = await node(current_state)
                else:
                    next_state = node(current_state)
                
                # Record execution event
                event = Event(
                    event_type=EventType.NODE_EXECUTED,
                    description=f"Node {node_name} executed",
                    timestamp=datetime.now(),
                    node_name=node_name,
                )
                current_state = next_state.with_event(event)
                
                if self.bus:
                    self.bus.observe("POST_NODE", current_state, node_name=node_name)
            
            except Exception as e:
                logger.error(f"Node {node_name} failed: {e}")
                
                if self.bus:
                    self.bus.observe("ERROR", current_state, node_name=node_name, error=e)
                
                if self.policy == Policy.STRICT:
                    raise
                
                # Record error event in EXPLORATION mode
                event = Event(
                    event_type=EventType.ERROR,
                    description=f"Node {node_name} failed: {e}",
                    timestamp=datetime.now(),
                    metadata={"ERROR": str(e)},
                    node_name=node_name,
                )
                current_state = current_state.with_event(event)
        
        if self.bus:
            self.bus.observe("GRAPH_END", current_state)
        
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
            self.bus.observe("GRAPH_START", state)
        
        current_state = state
        yield current_state  # Yield initial state
        
        for node in self.nodes:
            try:
                node_name = node.__name__
                
                if self.bus:
                    self.bus.observe("PRE_NODE", current_state, node_name=node_name)
                
                # Execute node
                if asyncio.iscoroutinefunction(node):
                    next_state = await node(current_state)
                else:
                    next_state = node(current_state)
                
                # Record execution event
                event = Event(
                    event_type=EventType.NODE_EXECUTED,
                    description=f"Node {node_name} executed",
                    timestamp=datetime.now(),
                )
                current_state = next_state.with_event(event)
                
                if self.bus:
                    self.bus.observe("POST_NODE", current_state, node_name=node_name)
                
                yield current_state  # Yield after each node
            
            except Exception as e:
                logger.error(f"Node {node_name} failed: {e}")
                
                if self.bus:
                    self.bus.observe("ERROR", current_state, node_name=node_name, error=e)
                
                if self.policy == Policy.STRICT:
                    raise
                
                # Record error and continue
                event = Event(
                    event_type=EventType.ERROR,
                    description=f"Node {node_name} failed: {e}",
                    timestamp=datetime.now(),
                    metadata={"ERROR": str(e)},
                    node_name=node_name,
                )
                current_state = current_state.with_event(event)
                yield current_state
        
        if self.bus:
            self.bus.observe("GRAPH_END", current_state)


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
            self.bus.observe("GRAPH_START", state)
        
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
                self.bus.observe("GRAPH_END", current_state)
            
            return current_state
        
        except Exception as e:
            if self.bus:
                self.bus.observe("ERROR", state, error=e)
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