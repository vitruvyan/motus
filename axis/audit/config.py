from enum import Enum
from dataclasses import dataclass
from typing import List, Optional

class BackupMode(Enum):
    """Backup frequency modes"""
    INCREMENTAL = "incremental"      # Every 30 min
    CRITICAL = "critical"            # Every 6 hours
    FULL_SYSTEM = "full_system"       # Daily
    DISASTER_RECOVERY = "disaster_recovery"  # On-demand

class VaultStatus(Enum):
    """Vault operation status"""
    INITIATED = "initiated"
    WATCHING = "watching"
    SEALING = "sealing"
    DEPARTING = "departing"
    SECURING = "securing"
    COMPLETED = "completed"
    FAILED = "failed"
    CORRUPTED = "corrupted"

@dataclass
class AuditConfig:
    """Configuration for Audit Layer"""
    
    # Database
    postgres_connection_string: str
    watched_tables: List[str]
    
    # Qdrant
    qdrant_url: str
    watched_collections: List[str]
    
    # Monitoring
    check_interval_seconds: int = 1800  # 30 minutes
    backup_mode: BackupMode = BackupMode.INCREMENTAL
    
    # Storage
    backup_storage_path: str = "/var/axis/backups"
    
    # Cloud upload
    cloud_provider: str = "local"  # local, s3, gdrive
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    gdrive_folder_id: Optional[str] = None
    gdrive_token_path: Optional[str] = None
    
    def __post_init__(self):
        if self.watched_tables is None:
            self.watched_tables = ["traces"]  # Default table
        if self.watched_collections is None:
            self.watched_collections = ["default_collection"]  # Default collection