from dataclasses import dataclass
from typing import Dict, Any, Optional
from pathlib import Path

@dataclass
class AuditEvent:
    """Event published by audit agents"""
    event_type: str
    payload: Dict[str, Any]
    timestamp: str
    agent_name: Optional[str] = None

@dataclass
class BackupResult:
    """Result of backup operation"""
    archive_path: Path
    sha256: str
    size_bytes: int
    backup_id: str
    timestamp: str

@dataclass
class UploadResult:
    """Result of upload operation"""
    destination_url: str
    upload_time_seconds: float
    provider: str

@dataclass
class VerificationResult:
    """Result of integrity verification"""
    verified: bool
    local_checksum: str
    remote_checksum: str
    mismatch_reason: Optional[str] = None