"""
Audit Orchestrator - Event-driven coordination of audit agents.
"""

import asyncio
from datetime import datetime
from typing import List, Optional

from axis import GraphState, Fact, Decision, Rejection, Event, SynapticBus
from .agents import SentinelAgent, ArchivistAgent, CourierAgent, ChamberlainAgent
from .config import AuditConfig
from .events import AuditEvent


class AuditOrchestrator:
    """
    Event-driven orchestrator for audit agents.
    Coordinates Sentinel → Archivist → Courier → Chamberlain pipeline.
    """

    def __init__(self, config: AuditConfig, bus: Optional[SynapticBus] = None):
        self.config = config
        self.bus = bus  # Optional SynapticBus for observability

        # Initialize agents
        self.sentinel = SentinelAgent(config)
        self.archivist = ArchivistAgent(config)
        self.courier = CourierAgent(config)
        self.chamberlain = ChamberlainAgent(config)

        # State tracking
        self.running = False
        self._audit_log: List[GraphState] = []

    async def start(self):
        """
        Start audit orchestration.

        Flow:
        1. Start Sentinel monitoring
        2. Listen for change events
        3. Trigger Archivist → Courier → Chamberlain pipeline
        4. Record results in GraphState
        5. Publish to SynapticBus (if available)
        """
        self.running = True

        # Start Sentinel in background
        sentinel_task = asyncio.create_task(self.sentinel.start_watch())

        # Event processing loop
        event_queue = self.sentinel.get_events()

        while self.running:
            try:
                # Wait for event with timeout
                event = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=5.0
                )

                # Process event
                await self._handle_event(event)

            except asyncio.TimeoutError:
                # No events, continue
                continue
            except Exception as e:
                # Log error but continue
                print(f"Error processing event: {e}")

        # Cleanup
        await self.sentinel.stop_watch()
        await sentinel_task

    async def stop(self):
        """Stop orchestration"""
        self.running = False

    async def _handle_event(self, event: AuditEvent):
        """
        Handle audit event from Sentinel.

        Pipeline:
        1. Archivist creates backup
        2. Courier uploads to cloud
        3. Chamberlain verifies integrity
        4. Record audit trail in GraphState
        """
        if event.event_type != "changes_detected":
            return

        # Create GraphState for audit trail
        trace_id = f"audit_{int(datetime.utcnow().timestamp())}"
        state = GraphState.empty(trace_id).with_intent("audit_backup")

        # Record detection
        state = state.with_fact(Fact(
            key="changes_detected",
            value={
                "event": "changes_detected",
                "changes": event.payload
            },
            source="sentinel_agent",
            timestamp=datetime.utcnow()
        ))

        # Step 1: Create backup
        backup_result = await self.archivist.execute_backup(
            backup_mode=self.config.backup_mode,
            changes=event.payload
        )

        state = state.with_fact(Fact(
            key="backup_created",
            value={
                "event": "backup_created",
                "archive_path": str(backup_result.archive_path),
                "sha256": backup_result.sha256,
                "size_bytes": backup_result.size_bytes
            },
            source="archivist_agent",
            timestamp=datetime.utcnow()
        ))

        # Step 2: Upload to cloud
        upload_result = await self.courier.upload(
            archive_path=backup_result.archive_path,
            backup_id=backup_result.backup_id
        )

        state = state.with_decision(Decision(
            description=f"Uploaded to {upload_result.destination_url}",
            timestamp=datetime.utcnow()
        ))

        # Step 3: Verify integrity
        verification_result = await self.chamberlain.verify(
            local_checksum=backup_result.sha256,
            destination_url=upload_result.destination_url
        )

        if verification_result.verified:
            state = state.with_fact(Fact(
                key="verification_success",
                value={
                    "event": "verification_success",
                    "local_checksum": verification_result.local_checksum,
                    "remote_checksum": verification_result.remote_checksum
                },
                source="chamberlain_agent",
                timestamp=datetime.utcnow()
            ))
        else:
            state = state.with_rejection(Rejection(
                description="verify_backup_integrity",
                reason=verification_result.mismatch_reason or "Checksum mismatch",
                timestamp=datetime.utcnow()
            ))

        # Store audit trail
        self._audit_log.append(state)

        # Publish to SynapticBus (if available)
        if self.bus:
            self.bus.on_node_completed(Event(
                event_type="audit_cycle_complete",
                description="Audit backup cycle completed",
                timestamp=datetime.utcnow(),
                metadata={
                    "trace_id": trace_id,
                    "verified": verification_result.verified
                },
                node_name="audit_orchestrator"
            ))

    def get_audit_log(self) -> List[GraphState]:
        """Return audit trail"""
        return self._audit_log