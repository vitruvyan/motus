import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

# These tests MOCK the adapters, but patching axis.persistence.<Adapter>
# walks the lazy __getattr__, which imports the real driver. On a bare
# `pip install vitruvyan-axis` that driver is absent by design — so the
# tests gate on the extras, like the live-service tests already do:
#   pip install vitruvyan-axis[postgres,qdrant]
pytest.importorskip("psycopg2", reason="audit tests need the [postgres] extra")
pytest.importorskip("httpx", reason="audit tests need the [qdrant] extra")

from axis.audit import (
    SentinelAgent,
    ArchivistAgent,
    CourierAgent,
    ChamberlainAgent,
    AuditOrchestrator,
    AuditConfig,
    BackupMode,
    AuditEvent,
    BackupResult,
    VerificationResult
)
from axis import GraphState

@pytest.fixture
def audit_config(tmp_path):
    """Test configuration with temp directories"""
    return AuditConfig(
        postgres_connection_string="postgresql://test:test@localhost:5432/test",
        watched_tables=["test_table"],
        qdrant_url="http://localhost:6333",
        watched_collections=["test_collection"],
        check_interval_seconds=1,
        backup_storage_path=str(tmp_path / "backups")
    )

@pytest.mark.asyncio
@patch('axis.persistence.PostgreSQLAdapter')
async def test_sentinel_detect_db_changes(mock_postgres_adapter, audit_config):
    """Test database change detection"""
    mock_postgres = Mock()
    mock_postgres_adapter.return_value = mock_postgres
    mock_postgres.list_traces.return_value = ["trace1", "trace2"]
    
    sentinel = SentinelAgent(audit_config)
    
    # Mock database changes
    changes = await sentinel._detect_changes()
    
    assert isinstance(changes, list)

@pytest.mark.asyncio
@patch('axis.persistence.PostgreSQLAdapter')
async def test_sentinel_detect_file_changes(mock_postgres_adapter, audit_config):
    """Test filesystem change detection"""
    mock_postgres = Mock()
    mock_postgres_adapter.return_value = mock_postgres
    
    sentinel = SentinelAgent(audit_config)
    
    # Create test file
    test_file = Path(audit_config.backup_storage_path) / "test.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("test content")
    
    # Detect changes
    changes = await sentinel._detect_changes()
    
    # Modify file
    test_file.write_text("modified content")
    
    # Detect again
    new_changes = await sentinel._detect_changes()
    
    assert len(new_changes) >= len(changes)

@pytest.mark.asyncio
@patch('axis.persistence.PostgreSQLAdapter')
async def test_sentinel_event_publishing(mock_postgres_adapter, audit_config):
    """Test event publishing to queue"""
    mock_postgres = Mock()
    mock_postgres_adapter.return_value = mock_postgres
    
    sentinel = SentinelAgent(audit_config)
    
    await sentinel._publish_event("test_event", {"data": "test"})
    
    event_queue = sentinel.get_events()
    event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
    
    assert event.event_type == "test_event"
    assert event.payload["data"] == "test"

@pytest.mark.asyncio
@patch('axis.persistence.PostgreSQLAdapter')
@patch('axis.persistence.QdrantAdapter')
async def test_archivist_incremental_backup(mock_qdrant_adapter, mock_postgres_adapter, audit_config):
    """Test incremental backup creation"""
    mock_postgres = Mock()
    mock_postgres_adapter.return_value = mock_postgres
    mock_postgres.list_traces.return_value = ["trace1", "trace2"]
    mock_postgres.load.side_effect = [Mock(to_dict=lambda: {"trace_id": "trace1"}), Mock(to_dict=lambda: {"trace_id": "trace2"})]
    
    mock_qdrant = Mock()
    mock_qdrant_adapter.return_value = mock_qdrant
    
    archivist = ArchivistAgent(audit_config)
    
    changes = [{"type": "database", "target": "test_table"}]
    
    result = await archivist.execute_backup(BackupMode.INCREMENTAL, changes)
    
    assert isinstance(result, BackupResult)
    assert result.backup_id.startswith("backup_")
    assert result.archive_path.exists()
    assert result.sha256
    assert result.size_bytes > 0
    assert result.timestamp

@pytest.mark.asyncio
@patch('axis.persistence.PostgreSQLAdapter')
@patch('axis.persistence.QdrantAdapter')
async def test_archivist_full_backup(mock_qdrant_adapter, mock_postgres_adapter, audit_config):
    """Test full system backup creation"""
    mock_postgres = Mock()
    mock_postgres_adapter.return_value = mock_postgres
    mock_postgres.list_traces.return_value = ["trace1"]
    mock_postgres.load.return_value = Mock(to_dict=lambda: {"trace_id": "trace1"})
    
    mock_qdrant = Mock()
    mock_qdrant_adapter.return_value = mock_qdrant
    
    archivist = ArchivistAgent(audit_config)
    
    result = await archivist.execute_backup(BackupMode.FULL_SYSTEM, [])
    
    assert isinstance(result, BackupResult)
    assert result.backup_id.startswith("backup_")
    assert result.archive_path.exists()
    assert result.sha256
    assert result.size_bytes > 0

@pytest.mark.asyncio
@patch('axis.persistence.PostgreSQLAdapter')
async def test_archivist_checksum_calculation(mock_postgres_adapter, audit_config):
    """Test SHA256 checksum calculation"""
    mock_postgres = Mock()
    mock_postgres_adapter.return_value = mock_postgres
    
    archivist = ArchivistAgent(audit_config)
    
    # Create a test file
    test_file = Path(audit_config.backup_storage_path) / "checksum_test.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_content = b"test content for checksum"
    test_file.write_bytes(test_content)
    
    # Calculate checksum
    checksum = await archivist._calculate_sha256(test_file)
    
    # Verify against known SHA256
    import hashlib
    expected = hashlib.sha256(test_content).hexdigest()
    
    assert checksum == expected

@pytest.mark.asyncio
async def test_courier_local_upload(audit_config):
    """Test local upload functionality"""
    courier = CourierAgent(audit_config)
    
    # Create a test archive file
    archive_path = Path(audit_config.backup_storage_path) / "test_backup.tar.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text("test archive content")
    
    backup_id = "test_backup_123"
    
    result = await courier.upload(archive_path, backup_id)
    
    assert result.provider == "local"
    assert result.destination_url.startswith("file://")
    assert result.upload_time_seconds >= 0
    assert "cloud" in result.destination_url
    assert archive_path.name in result.destination_url

@pytest.mark.asyncio
async def test_courier_retry_logic(audit_config):
    """Test retry logic with exponential backoff"""
    courier = CourierAgent(audit_config)
    
    # Create a test archive file
    archive_path = Path(audit_config.backup_storage_path) / "test_backup.tar.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text("test archive content")
    
    backup_id = "test_backup_123"
    
    # Mock a failing upload function
    call_count = 0
    async def failing_upload(archive_path, backup_id):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("Upload failed")
        return await courier._upload_local(archive_path, backup_id)
    
    # Test retry
    result = await courier._upload_with_retry(failing_upload, archive_path, backup_id, max_retries=3)
    
    assert call_count == 3
    assert result.startswith("file://")

@pytest.mark.asyncio
async def test_courier_fallback_on_missing_deps(audit_config):
    """Test fallback to local when cloud dependencies are missing"""
    # Test S3 fallback
    audit_config.cloud_provider = "s3"
    audit_config.s3_bucket = "test-bucket"
    audit_config.s3_region = "us-east-1"
    
    courier = CourierAgent(audit_config)
    
    # Create a test archive file
    archive_path = Path(audit_config.backup_storage_path) / "test_backup.tar.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text("test archive content")
    
    backup_id = "test_backup_123"
    
    # This should fallback to local since boto3 is not installed
    result = await courier.upload(archive_path, backup_id)
    
    assert result.provider == "s3"  # Provider is still s3, but it fell back
    assert result.destination_url.startswith("file://")  # But uploaded locally

@pytest.mark.asyncio
async def test_chamberlain_local_verification(audit_config):
    """Test local file verification"""
    chamberlain = ChamberlainAgent(audit_config)
    
    # Create a test file
    test_file = Path(audit_config.backup_storage_path) / "test_file.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("test content for verification")
    
    # Calculate expected checksum
    import hashlib
    expected_checksum = hashlib.sha256(b"test content for verification").hexdigest()
    
    # Test verification
    result = await chamberlain.verify(expected_checksum, f"file://{test_file}")
    
    assert result.verified is True
    assert result.local_checksum == expected_checksum
    assert result.remote_checksum == expected_checksum
    assert result.mismatch_reason is None

@pytest.mark.asyncio
async def test_chamberlain_checksum_match(audit_config):
    """Test checksum verification when checksums match"""
    chamberlain = ChamberlainAgent(audit_config)
    
    # Create a test file
    test_file = Path(audit_config.backup_storage_path) / "match_test.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_content = b"matching content"
    test_file.write_bytes(test_content)
    
    # Calculate checksum
    import hashlib
    checksum = hashlib.sha256(test_content).hexdigest()
    
    # Test verification
    result = await chamberlain.verify(checksum, f"file://{test_file}")
    
    assert result.verified is True
    assert result.local_checksum == checksum
    assert result.remote_checksum == checksum
    assert result.mismatch_reason is None

@pytest.mark.asyncio
async def test_chamberlain_checksum_mismatch(audit_config):
    """Test checksum verification when checksums don't match"""
    chamberlain = ChamberlainAgent(audit_config)
    
    # Create a test file
    test_file = Path(audit_config.backup_storage_path) / "mismatch_test.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("actual content")
    
    # Use a different checksum
    wrong_checksum = "wrong_checksum_1234567890123456789012345678901234567890"
    
    # Test verification
    result = await chamberlain.verify(wrong_checksum, f"file://{test_file}")
    
    assert result.verified is False
    assert result.local_checksum == wrong_checksum
    assert result.remote_checksum != wrong_checksum
    assert result.mismatch_reason == "Checksum mismatch"

# AuditOrchestrator Integration Tests

@pytest.mark.asyncio
@patch('axis.persistence.PostgreSQLAdapter')
@patch('axis.persistence.QdrantAdapter')
async def test_orchestrator_full_pipeline(mock_qdrant_adapter, mock_postgres_adapter, audit_config):
    """Test full audit pipeline end-to-end"""
    mock_postgres = Mock()
    mock_postgres_adapter.return_value = mock_postgres
    mock_postgres.list_traces.return_value = ["trace1"]
    mock_postgres.load.return_value = Mock(to_dict=lambda: {"trace_id": "trace1"})

    mock_qdrant = Mock()
    mock_qdrant_adapter.return_value = mock_qdrant

    orchestrator = AuditOrchestrator(audit_config)

    # Simulate change event
    event = AuditEvent(
        event_type="changes_detected",
        payload=[{"type": "database", "target": "test_table"}],
        timestamp=datetime.utcnow().isoformat(),
        agent_name="sentinel"
    )

    # Process event
    await orchestrator._handle_event(event)

    # Check audit log
    audit_log = orchestrator.get_audit_log()

    assert len(audit_log) == 1
    state = audit_log[0]

    # Verify pipeline executed
    assert len(state.facts) >= 2  # detection + backup + verification
    assert any(f.source == "sentinel_agent" for f in state.facts)
    assert any(f.source == "archivist_agent" for f in state.facts)

@pytest.mark.asyncio
@patch('axis.persistence.PostgreSQLAdapter')
@patch('axis.persistence.QdrantAdapter')
async def test_orchestrator_graphstate_recording(mock_qdrant_adapter, mock_postgres_adapter, audit_config):
    """Test GraphState audit trail recording"""
    mock_postgres = Mock()
    mock_postgres_adapter.return_value = mock_postgres
    mock_postgres.list_traces.return_value = ["trace1"]
    mock_postgres.load.return_value = Mock(to_dict=lambda: {"trace_id": "trace1"})

    mock_qdrant = Mock()
    mock_qdrant_adapter.return_value = mock_qdrant

    orchestrator = AuditOrchestrator(audit_config)

    event = AuditEvent(
        event_type="changes_detected",
        payload=[{"type": "filesystem", "target": "/app/config"}],
        timestamp=datetime.utcnow().isoformat()
    )

    await orchestrator._handle_event(event)

    audit_log = orchestrator.get_audit_log()

    assert len(audit_log) > 0
    state = audit_log[0]

    # Verify immutability
    assert isinstance(state, GraphState)
    assert state.trace_id.startswith("audit_")

@pytest.mark.asyncio
@patch('axis.persistence.PostgreSQLAdapter')
@patch('axis.persistence.QdrantAdapter')
async def test_orchestrator_synaptic_bus_integration(mock_qdrant_adapter, mock_postgres_adapter, audit_config):
    """Test SynapticBus event publishing"""
    mock_postgres = Mock()
    mock_postgres_adapter.return_value = mock_postgres
    mock_postgres.list_traces.return_value = ["trace1"]
    mock_postgres.load.return_value = Mock(to_dict=lambda: {"trace_id": "trace1"})

    mock_qdrant = Mock()
    mock_qdrant_adapter.return_value = mock_qdrant

    from axis import SynapticBus

    bus = SynapticBus()
    events_received = []

    def observer(event):
        events_received.append(event)

    bus.on_node_completed = observer

    orchestrator = AuditOrchestrator(audit_config, bus=bus)

    event = AuditEvent(
        event_type="changes_detected",
        payload=[{"type": "database", "target": "test"}],
        timestamp=datetime.utcnow().isoformat()
    )

    await orchestrator._handle_event(event)

    # Check SynapticBus received event
    assert len(events_received) > 0