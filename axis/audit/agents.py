import asyncio
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from urllib.parse import urlparse

from .config import AuditConfig, BackupMode
from .events import AuditEvent, BackupResult, UploadResult, VerificationResult


class SentinelAgent:
    """
    Async change detection agent.
    Monitors database tables and filesystem for changes requiring backup.
    """

    def __init__(self, config: AuditConfig):
        # Imported here, not at module level: PostgreSQLAdapter/QdrantAdapter
        # pull in psycopg2/httpx, and not every consumer of this package
        # has those installed just because they imported `axis`.
        from axis.persistence import PostgreSQLAdapter, QdrantAdapter

        self.config = config
        try:
            # Parse PostgreSQL connection string
            parsed = urlparse(config.postgres_connection_string)
            host = parsed.hostname or "localhost"
            port = parsed.port or 5432
            database = parsed.path.lstrip("/") or "axis"
            user = parsed.username or "axis"
            password = parsed.password or ""

            self.postgres = PostgreSQLAdapter(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password
            )
        except Exception:
            # Handle connection failures gracefully
            self.postgres = None

        self.qdrant = QdrantAdapter(url=config.qdrant_url)
        self.watching = False
        self.last_check = datetime.utcnow()
        self._last_checksums: Dict[str, str] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue()

    async def start_watch(self):
        """Begin continuous monitoring loop"""
        self.watching = True
        while self.watching:
            changes = await self._detect_changes()
            if changes:
                await self._publish_event("changes_detected", changes)
            await asyncio.sleep(self.config.check_interval_seconds)

    async def stop_watch(self):
        """Stop monitoring"""
        self.watching = False

    async def _detect_changes(self) -> List[Dict[str, Any]]:
        """
        Detect database and filesystem changes.

        Logic from Vitruvyan:
        - Database: Calculate table checksums via PostgreSQL
        - Filesystem: Calculate file MD5 hashes
        - Compare with last known state

        Returns:
            List of change records with:
            - type: "database" or "filesystem"
            - target: table name or file path
            - checksum: current checksum
            - previous_checksum: last known checksum
            - timestamp: detection time
        """
        changes = []

        # Database change detection
        db_changes = await self._detect_database_changes()
        changes.extend(db_changes)

        # Filesystem change detection
        fs_changes = await self._detect_filesystem_changes()
        changes.extend(fs_changes)

        return changes

    async def _detect_database_changes(self) -> List[Dict[str, Any]]:
        """Detect changes in watched database tables"""
        changes = []

        for table in self.config.watched_tables:
            try:
                # Calculate current table checksum
                # Using a simple approach - count rows and last modified time
                # In a real implementation, this would use more sophisticated checksums
                current_checksum = await self._calculate_table_checksum(table)
                previous_checksum = self._last_checksums.get(f"db:{table}", "")

                if current_checksum != previous_checksum and previous_checksum:
                    changes.append({
                        "type": "database",
                        "target": table,
                        "checksum": current_checksum,
                        "previous_checksum": previous_checksum,
                        "timestamp": datetime.utcnow().isoformat()
                    })

                self._last_checksums[f"db:{table}"] = current_checksum

            except Exception as e:
                # Log error but continue with other tables
                print(f"Error checking table {table}: {e}")

        return changes

    async def _detect_filesystem_changes(self) -> List[Dict[str, Any]]:
        """Detect changes in filesystem"""
        changes = []

        # For now, monitor the backup storage path itself
        # In a full implementation, this would monitor multiple paths
        try:
            backup_path = Path(self.config.backup_storage_path)
            if backup_path.exists():
                current_checksum = await self._calculate_file_checksum(str(backup_path))
                previous_checksum = self._last_checksums.get(f"fs:{backup_path}", "")

                if current_checksum != previous_checksum and previous_checksum:
                    changes.append({
                        "type": "filesystem",
                        "target": str(backup_path),
                        "checksum": current_checksum,
                        "previous_checksum": previous_checksum,
                        "timestamp": datetime.utcnow().isoformat()
                    })

                self._last_checksums[f"fs:{backup_path}"] = current_checksum

        except Exception as e:
            print(f"Error checking filesystem: {e}")

        return changes

    async def _calculate_table_checksum(self, table_name: str) -> str:
        """Calculate a simple checksum for a database table"""
        try:
            if self.postgres is None:
                # Return a mock checksum if no database connection
                return hashlib.md5(f"{table_name}:mock:{datetime.utcnow().isoformat()}".encode()).hexdigest()
            
            # Use the number of traces as a simple checksum
            # In a real implementation, this would be more sophisticated
            traces = self.postgres.list_traces(limit=1000)
            trace_count = len(traces)
            # Create a hash based on table name and trace count
            return hashlib.md5(f"{table_name}:{trace_count}:{datetime.utcnow().isoformat()}".encode()).hexdigest()
        except Exception:
            return ""

    async def _calculate_file_checksum(self, file_path: str) -> str:
        """Calculate MD5 checksum of a file or directory"""
        try:
            path = Path(file_path)
            if path.is_file():
                # File checksum
                hash_md5 = hashlib.md5()
                with open(path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash_md5.update(chunk)
                return hash_md5.hexdigest()
            elif path.is_dir():
                # Directory checksum based on file listing
                files = list(path.rglob("*"))
                dir_content = "|".join(sorted([str(f.relative_to(path)) for f in files if f.is_file()]))
                return hashlib.md5(dir_content.encode()).hexdigest()
            else:
                return ""
        except Exception:
            return ""

    async def _publish_event(self, event_type: str, payload: Dict[str, Any]):
        """Publish event to queue for orchestrator"""
        event = AuditEvent(
            event_type=event_type,
            payload=payload,
            timestamp=datetime.utcnow().isoformat(),
            agent_name="SentinelAgent"
        )
        await self._event_queue.put(event)

    def get_events(self) -> asyncio.Queue:
        """Return event queue for orchestrator to consume"""
        return self._event_queue


class ArchivistAgent:
    """
    Async backup creation agent.
    Creates tar.gz archives of changed data with SHA256 checksums.
    """
    
    def __init__(self, config: AuditConfig):
        from axis.persistence import PostgreSQLAdapter, QdrantAdapter

        self.config = config
        try:
            # Parse PostgreSQL connection string
            parsed = urlparse(config.postgres_connection_string)
            host = parsed.hostname or "localhost"
            port = parsed.port or 5432
            database = parsed.path.lstrip("/") or "axis"
            user = parsed.username or "axis"
            password = parsed.password or ""

            self.postgres = PostgreSQLAdapter(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password
            )
        except Exception:
            # Handle connection failures gracefully
            self.postgres = None

        self.qdrant = QdrantAdapter(url=config.qdrant_url)
        self.archive_base = Path(config.backup_storage_path)
        self.archive_base.mkdir(parents=True, exist_ok=True)
    
    async def execute_backup(
        self, 
        backup_mode: BackupMode, 
        changes: List[Dict[str, Any]]
    ) -> BackupResult:
        """
        Execute backup based on detected changes.
        
        Logic from Vitruvyan:
        1. Create timestamped backup directory
        2. Export PostgreSQL tables (pg_dump equivalent)
        3. Export Qdrant collections (snapshot)
        4. Create tar.gz archive
        5. Calculate SHA256 checksum
        6. Return backup metadata
        
        Args:
            backup_mode: Incremental, critical, full_system
            changes: List of changes from SentinelAgent
            
        Returns:
            BackupResult with:
            - archive_path: Path to tar.gz
            - sha256: Archive checksum
            - size_bytes: Archive size
            - backup_id: Unique identifier
            - timestamp: Creation time
        """
        backup_id = f"backup_{int(datetime.utcnow().timestamp())}"
        
        # Create backup directory
        backup_dir = self.archive_base / backup_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Export data
        if backup_mode == BackupMode.INCREMENTAL:
            await self._backup_incremental(backup_dir, changes)
        elif backup_mode == BackupMode.FULL_SYSTEM:
            await self._backup_full_system(backup_dir)
        
        # Create archive
        archive_path = await self._create_archive(backup_dir, backup_id)
        
        # Calculate checksum
        sha256 = await self._calculate_sha256(archive_path)
        
        # Clean up temp directory
        shutil.rmtree(backup_dir)
        
        return BackupResult(
            archive_path=archive_path,
            sha256=sha256,
            size_bytes=archive_path.stat().st_size,
            backup_id=backup_id,
            timestamp=datetime.utcnow().isoformat()
        )
    
    async def _backup_incremental(
        self, 
        backup_dir: Path, 
        changes: List[Dict[str, Any]]
    ):
        """
        Backup only changed tables/collections.
        
        Port logic from Vitruvyan archivist.py:
        - For database changes: Export specific tables to JSON
        - For Qdrant changes: Export collection snapshots
        """
        for change in changes:
            if change["type"] == "database":
                await self._export_table(backup_dir, change["target"])
            elif change["type"] == "qdrant":
                await self._export_collection(backup_dir, change["target"])
    
    async def _backup_full_system(self, backup_dir: Path):
        """
        Full system backup (all tables + collections).
        
        Port logic from Vitruvyan:
        - Export all watched tables
        - Export all watched collections
        - Export configuration files
        """
        # Export all tables
        for table in self.config.watched_tables:
            await self._export_table(backup_dir, table)
        
        # Export all Qdrant collections
        for collection in self.config.watched_collections:
            await self._export_collection(backup_dir, collection)
    
    async def _export_table(self, backup_dir: Path, table_name: str):
        """
        Export PostgreSQL table to JSON.
        
        Use PostgreSQLAdapter (no raw SQL)
        """
        # For now, export all traces since we don't have table-specific export
        if self.postgres is None:
            # Mock data if no database connection
            states = []
        else:
            # Get all trace IDs and load states
            trace_ids = self.postgres.list_traces(limit=10000)
            states = []
            for trace_id in trace_ids:
                state = self.postgres.load(trace_id)
                if state:
                    states.append(state)
        
        # Write to JSON
        output_file = backup_dir / f"{table_name}.json"
        with open(output_file, "w") as f:
            json.dump([s.to_dict() for s in states], f, indent=2)
    
    async def _export_collection(self, backup_dir: Path, collection_name: str):
        """
        Export Qdrant collection to JSON.
        
        Use QdrantAdapter scroll to get all points
        """
        try:
            # Use scroll API to get all points
            points = await self._get_all_points(collection_name)
        except Exception:
            # Mock data if Qdrant not available
            points = []
        
        # Write to JSON
        output_file = backup_dir / f"{collection_name}_vectors.json"
        with open(output_file, "w") as f:
            json.dump(points, f, indent=2)
    
    async def _get_all_points(self, collection_name: str) -> List[Dict[str, Any]]:
        """
        Get all points from a Qdrant collection using scroll API.
        """
        # This is a simplified implementation
        # In practice, would need to handle pagination with offset
        try:
            # For embeddings collection
            if collection_name == "embeddings":
                payload = {
                    "limit": 10000,
                    "with_payload": True,
                    "with_vectors": True
                }
                result = self.qdrant._request("POST", f"/collections/{self.qdrant.embeddings_collection}/points/scroll", payload)
                points = result["result"]["points"]
                return [p for p in points]
            else:
                # For states collection
                payload = {
                    "limit": 10000,
                    "with_payload": True,
                    "with_vectors": False
                }
                result = self.qdrant._request("POST", f"/collections/{self.qdrant.states_collection}/points/scroll", payload)
                points = result["result"]["points"]
                return [p["payload"] for p in points if "payload" in p]
        except Exception:
            return []
    
    async def _create_archive(self, backup_dir: Path, backup_id: str) -> Path:
        """
        Create tar.gz archive from backup directory.
        
        Port logic from Vitruvyan:
        - Compression level 6 (balance speed/size)
        - Tar format for cross-platform compatibility
        """
        archive_path = self.archive_base / f"{backup_id}.tar.gz"
        
        with tarfile.open(archive_path, "w:gz", compresslevel=6) as tar:
            tar.add(backup_dir, arcname=backup_id)
        
        return archive_path
    
    async def _calculate_sha256(self, file_path: Path) -> str:
        """Calculate SHA256 checksum for integrity verification"""
        sha256_hash = hashlib.sha256()
        
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        
        return sha256_hash.hexdigest()


class CourierAgent:
    """
    Async cloud upload agent.
    Uploads backup archives to S3, Google Drive, or local storage.
    """
    
    def __init__(self, config: AuditConfig):
        self.config = config
        self._upload_strategies = {
            "local": self._upload_local,
            "s3": self._upload_s3,
            "gdrive": self._upload_gdrive
        }
    
    async def upload(
        self, 
        archive_path: Path, 
        backup_id: str
    ) -> UploadResult:
        """
        Upload archive to configured destination.
        
        Args:
            archive_path: Path to tar.gz archive
            backup_id: Unique backup identifier
            
        Returns:
            UploadResult with:
            - destination_url: Where archive was uploaded
            - upload_time_seconds: Time taken
            - provider: "local", "s3", or "gdrive"
        """
        provider = self.config.cloud_provider
        
        if provider not in self._upload_strategies:
            raise ValueError(f"Unknown provider: {provider}")
        
        start_time = time.time()
        
        # Execute upload with retry
        destination_url = await self._upload_with_retry(
            self._upload_strategies[provider],
            archive_path,
            backup_id
        )
        
        elapsed = time.time() - start_time
        
        return UploadResult(
            destination_url=destination_url,
            upload_time_seconds=elapsed,
            provider=provider
        )
    
    async def _upload_with_retry(
        self, 
        upload_func: Callable, 
        archive_path: Path, 
        backup_id: str,
        max_retries: int = 3
    ) -> str:
        """
        Retry upload on failure with exponential backoff.
        
        Port logic from Vitruvyan:
        - Retry up to 3 times
        - Exponential backoff: 0.5s, 1s, 2s
        - Log failures
        """
        for attempt in range(max_retries):
            try:
                return await upload_func(archive_path, backup_id)
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 0.5 * (2 ** attempt)
                    await asyncio.sleep(wait_time)
                else:
                    raise
    
    async def _upload_local(self, archive_path: Path, backup_id: str) -> str:
        """
        Copy archive to local backup directory.
        
        Simple file copy (no network)
        """
        destination = Path(self.config.backup_storage_path) / "cloud" / archive_path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        
        shutil.copy2(archive_path, destination)
        
        return f"file://{destination.absolute()}"
    
    async def _upload_s3(self, archive_path: Path, backup_id: str) -> str:
        """
        Upload to AWS S3 using boto3.
        
        IMPORTANT: boto3 is external dependency.
        Keep implementation minimal, document as optional.
        
        If boto3 not installed, log warning and fallback to local.
        """
        try:
            import boto3
            
            s3_client = boto3.client(
                's3',
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=self.config.s3_region
            )
            
            key = f"axis-backups/{backup_id}/{archive_path.name}"
            
            # Upload with progress (async wrapper)
            await asyncio.to_thread(
                s3_client.upload_file,
                str(archive_path),
                self.config.s3_bucket,
                key
            )
            
            return f"s3://{self.config.s3_bucket}/{key}"
            
        except ImportError:
            # boto3 not installed, fallback to local
            return await self._upload_local(archive_path, backup_id)
    
    async def _upload_gdrive(self, archive_path: Path, backup_id: str) -> str:
        """
        Upload to Google Drive using google-api-python-client.
        
        IMPORTANT: google-api-python-client is external dependency.
        Keep implementation minimal, document as optional.
        
        If not installed, log warning and fallback to local.
        """
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            
            # Load OAuth token (pre-configured)
            token_path = self.config.gdrive_token_path
            if not os.path.exists(token_path):
                return await self._upload_local(archive_path, backup_id)
            
            creds = Credentials.from_authorized_user_file(token_path)
            service = build('drive', 'v3', credentials=creds)
            
            file_metadata = {
                'name': archive_path.name,
                'parents': [self.config.gdrive_folder_id] if self.config.gdrive_folder_id else []
            }
            
            media = MediaFileUpload(str(archive_path), resumable=True)
            
            # Upload (async wrapper)
            file = await asyncio.to_thread(
                service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id'
                ).execute
            )
            
            return f"gdrive://{file['id']}"
            
        except ImportError:
            # google-api-python-client not installed
            return await self._upload_local(archive_path, backup_id)


class ChamberlainAgent:
    """
    Async integrity verification agent.
    Verifies backup integrity by comparing local and remote checksums.
    """
    
    def __init__(self, config: AuditConfig):
        self.config = config
    
    async def verify(
        self, 
        local_checksum: str, 
        destination_url: str
    ) -> VerificationResult:
        """
        Verify backup integrity.
        
        Logic:
        1. Download remote archive (or access if local)
        2. Calculate SHA256 checksum
        3. Compare with local checksum
        4. Return verification result
        
        Args:
            local_checksum: SHA256 from ArchivistAgent
            destination_url: Upload destination from CourierAgent
            
        Returns:
            VerificationResult with:
            - verified: True if checksums match
            - local_checksum: Local SHA256
            - remote_checksum: Remote SHA256
            - mismatch_reason: If verification fails
        """
        # Parse destination URL
        if destination_url.startswith("file://"):
            remote_checksum = await self._verify_local(destination_url)
        elif destination_url.startswith("s3://"):
            remote_checksum = await self._verify_s3(destination_url)
        elif destination_url.startswith("gdrive://"):
            remote_checksum = await self._verify_gdrive(destination_url)
        else:
            raise ValueError(f"Unknown destination protocol: {destination_url}")
        
        verified = local_checksum == remote_checksum
        
        return VerificationResult(
            verified=verified,
            local_checksum=local_checksum,
            remote_checksum=remote_checksum,
            mismatch_reason=None if verified else "Checksum mismatch"
        )
    
    async def _verify_local(self, file_url: str) -> str:
        """
        Verify local file checksum.
        
        Simple: Just calculate SHA256 of file at path.
        """
        file_path = Path(file_url.replace("file://", ""))
        return await self._calculate_sha256(file_path)
    
    async def _verify_s3(self, s3_url: str) -> str:
        """
        Verify S3 object checksum.
        
        Logic:
        1. Download file to temp directory
        2. Calculate SHA256
        3. Clean up temp file
        
        Use boto3 (optional dependency)
        """
        try:
            import boto3
            
            # Parse S3 URL: s3://bucket/key
            parts = s3_url.replace("s3://", "").split("/", 1)
            bucket, key = parts[0], parts[1]
            
            s3_client = boto3.client(
                's3',
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
            )
            
            # Download to temp file
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                await asyncio.to_thread(
                    s3_client.download_file,
                    bucket,
                    key,
                    tmp.name
                )
                temp_path = Path(tmp.name)
            
            # Calculate checksum
            checksum = await self._calculate_sha256(temp_path)
            
            # Clean up
            temp_path.unlink()
            
            return checksum
            
        except ImportError:
            # boto3 not available, cannot verify
            return "VERIFICATION_SKIPPED_NO_BOTO3"
    
    async def _verify_gdrive(self, gdrive_url: str) -> str:
        """
        Verify Google Drive file checksum.
        
        Logic:
        1. Download file using Drive API
        2. Calculate SHA256
        3. Clean up temp file
        
        Use google-api-python-client (optional dependency)
        """
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            import io
            
            # Parse gdrive URL: gdrive://file_id
            file_id = gdrive_url.replace("gdrive://", "")
            
            token_path = self.config.gdrive_token_path
            if not os.path.exists(token_path):
                return "VERIFICATION_SKIPPED_NO_TOKEN"
            
            creds = Credentials.from_authorized_user_file(token_path)
            service = build('drive', 'v3', credentials=creds)
            
            # Download file
            request = service.files().get_media(fileId=file_id)
            
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                downloader = io.FileIO(tmp.name, 'wb')
                
                await asyncio.to_thread(
                    lambda: request.execute()  # Download chunks
                )
                
                temp_path = Path(tmp.name)
            
            # Calculate checksum
            checksum = await self._calculate_sha256(temp_path)
            
            # Clean up
            temp_path.unlink()
            
            return checksum
            
        except ImportError:
            return "VERIFICATION_SKIPPED_NO_GDRIVE_API"
    
    async def _calculate_sha256(self, file_path: Path) -> str:
        """Calculate SHA256 checksum (reuse from ArchivistAgent logic)"""
        sha256_hash = hashlib.sha256()
        
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        
        return sha256_hash.hexdigest()