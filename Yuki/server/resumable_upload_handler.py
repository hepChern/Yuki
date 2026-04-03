"""
Resumable upload handler for chunked file uploads.

This module provides server-side support for chunked, resumable uploads
with state persistence and chunk verification.
"""
import os
import json
import hashlib
import tarfile
import tempfile
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Set, Optional
from logging import getLogger


logger = getLogger("YukiLogger")


@dataclass
class ServerUploadState:
    """Server-side upload state for tracking chunked uploads.

    Attributes:
        upload_id: Unique upload identifier
        file_size: Total expected file size
        file_md5: MD5 hash of the complete file
        chunk_size: Size of each chunk in bytes
        total_chunks: Total number of chunks expected
        completed_chunks: Set of chunk indices received
        storage_path: Path where chunks are stored
        project_uuid: Project identifier
        impression_uuid: Impression identifier
        finalized: Whether upload has been finalized
    """
    upload_id: str
    file_size: int
    file_md5: str
    chunk_size: int
    total_chunks: int
    completed_chunks: Set[int]
    storage_path: str
    project_uuid: str
    impression_uuid: str
    finalized: bool = False

    def to_dict(self) -> dict:
        """Convert state to dictionary for JSON serialization."""
        return {
            **asdict(self),
            'completed_chunks': list(self.completed_chunks)
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ServerUploadState':
        """Create ServerUploadState from dictionary."""
        data['completed_chunks'] = set(data['completed_chunks'])
        return cls(**data)


class ResumableUploadManager:
    """Manages resumable uploads on the server.

    Handles chunk storage, verification, and assembly of uploaded files.
    Upload state is persisted to disk for recovery across server restarts.

    Attributes:
        STATE_DIR: Directory for persisting upload state files
        CHUNK_DIR: Directory for storing uploaded chunks
    """

    def __init__(self, base_storage_path: str):
        """Initialize the upload manager.

        Args:
            base_storage_path: Base path for Yuki storage
        """
        self.base_storage_path = base_storage_path
        self.STATE_DIR = Path(base_storage_path) / ".uploads" / "state"
        self.CHUNK_DIR = Path(base_storage_path) / ".uploads" / "chunks"

        # Ensure directories exist
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.CHUNK_DIR.mkdir(parents=True, exist_ok=True)

    def create_upload(
        self,
        upload_id: str,
        file_size: int,
        file_md5: str,
        chunk_size: int,
        total_chunks: int,
        project_uuid: str,
        impression_uuid: str
    ) -> ServerUploadState:
        """Create a new upload session.

        Args:
            upload_id: Unique upload identifier
            file_size: Total expected file size
            file_md5: MD5 hash of the complete file
            chunk_size: Size of each chunk
            total_chunks: Total number of chunks
            project_uuid: Project identifier
            impression_uuid: Impression identifier

        Returns:
            ServerUploadState for the new upload
        """
        state = ServerUploadState(
            upload_id=upload_id,
            file_size=file_size,
            file_md5=file_md5,
            chunk_size=chunk_size,
            total_chunks=total_chunks,
            completed_chunks=set(),
            storage_path=str(self.CHUNK_DIR / upload_id),
            project_uuid=project_uuid,
            impression_uuid=impression_uuid,
            finalized=False
        )

        # Create chunk storage directory
        chunk_dir = Path(state.storage_path)
        chunk_dir.mkdir(parents=True, exist_ok=True)

        self._save_state(state)
        logger.info(f"Created upload session {upload_id} for {project_uuid}/{impression_uuid}")

        return state

    def get_upload(self, upload_id: str) -> Optional[ServerUploadState]:
        """Get upload state by ID.

        Args:
            upload_id: Upload identifier

        Returns:
            ServerUploadState if found, None otherwise
        """
        state_file = self.STATE_DIR / f"{upload_id}.json"
        if not state_file.exists():
            return None

        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return ServerUploadState.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error(f"Failed to load upload state {upload_id}: {e}")
            return None

    def store_chunk(
        self,
        upload_id: str,
        chunk_index: int,
        chunk_data: bytes,
        chunk_md5: str
    ) -> bool:
        """Store an uploaded chunk.

        Args:
            upload_id: Upload identifier
            chunk_index: Index of the chunk
            chunk_data: Binary chunk data
            chunk_md5: Expected MD5 hash of chunk

        Returns:
            True if chunk was stored successfully, False otherwise
        """
        # Verify chunk MD5
        actual_md5 = hashlib.md5(chunk_data).hexdigest()
        if actual_md5 != chunk_md5:
            logger.warning(f"Chunk {chunk_index} MD5 mismatch for upload {upload_id}")
            return False

        # Get upload state
        state = self.get_upload(upload_id)
        if state is None:
            logger.error(f"Upload {upload_id} not found")
            return False

        if state.finalized:
            logger.error(f"Upload {upload_id} already finalized")
            return False

        # Store chunk
        chunk_path = Path(state.storage_path) / f"chunk_{chunk_index}"
        try:
            with open(chunk_path, 'wb') as f:
                f.write(chunk_data)

            # Update state
            state.completed_chunks.add(chunk_index)
            self._save_state(state)

            logger.debug(f"Stored chunk {chunk_index} for upload {upload_id}")
            return True

        except OSError as e:
            logger.error(f"Failed to store chunk {chunk_index} for upload {upload_id}: {e}")
            return False

    def get_completed_chunks(self, upload_id: str) -> Set[int]:
        """Get set of completed chunk indices.

        Args:
            upload_id: Upload identifier

        Returns:
            Set of completed chunk indices
        """
        state = self.get_upload(upload_id)
        if state is None:
            return set()
        return state.completed_chunks.copy()

    def is_upload_complete(self, upload_id: str) -> bool:
        """Check if all chunks have been uploaded.

        Args:
            upload_id: Upload identifier

        Returns:
            True if upload is complete, False otherwise
        """
        state = self.get_upload(upload_id)
        if state is None:
            return False
        return len(state.completed_chunks) == state.total_chunks

    def finalize_upload(
        self,
        upload_id: str,
        project_uuid: str,
        impression_uuid: str
    ) -> Optional[str]:
        """Finalize upload by assembling chunks and extracting.

        Args:
            upload_id: Upload identifier
            project_uuid: Project identifier
            impression_uuid: Impression identifier

        Returns:
            Path to extracted directory if successful, None otherwise
        """
        state = self.get_upload(upload_id)
        if state is None:
            logger.error(f"Upload {upload_id} not found")
            return None

        if not self.is_upload_complete(upload_id):
            logger.error(f"Upload {upload_id} incomplete")
            return None

        if state.finalized:
            logger.info(f"Upload {upload_id} already finalized")
            return self._get_extract_path(project_uuid, impression_uuid)

        # Assemble chunks into complete file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.tar.gz')
        try:
            with open(temp_file.name, 'wb') as outfile:
                for i in range(state.total_chunks):
                    chunk_path = Path(state.storage_path) / f"chunk_{i}"
                    if not chunk_path.exists():
                        logger.error(f"Missing chunk {i} for upload {upload_id}")
                        return None

                    with open(chunk_path, 'rb') as infile:
                        outfile.write(infile.read())

            # Verify complete file MD5
            file_md5 = self._calculate_file_md5(temp_file.name)
            if file_md5 != state.file_md5:
                logger.error(f"File MD5 mismatch for upload {upload_id}")
                return None

            # Extract to target directory
            extract_path = self._get_extract_path(project_uuid, impression_uuid)
            os.makedirs(extract_path, exist_ok=True)

            with tarfile.open(temp_file.name, 'r:gz') as tar:
                tar.extractall(extract_path)

            # Mark as finalized
            state.finalized = True
            self._save_state(state)

            logger.info(f"Finalized upload {upload_id} to {extract_path}")
            return extract_path

        except Exception as e:
            logger.error(f"Failed to finalize upload {upload_id}: {e}")
            return None
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_file.name)
            except OSError:
                pass

    def cancel_upload(self, upload_id: str) -> bool:
        """Cancel and clean up an upload.

        Args:
            upload_id: Upload identifier

        Returns:
            True if cancelled successfully, False otherwise
        """
        state = self.get_upload(upload_id)
        if state is None:
            return True  # Already doesn't exist

        try:
            # Remove chunk directory
            import shutil
            chunk_dir = Path(state.storage_path)
            if chunk_dir.exists():
                shutil.rmtree(chunk_dir)

            # Remove state file
            state_file = self.STATE_DIR / f"{upload_id}.json"
            state_file.unlink(missing_ok=True)

            logger.info(f"Cancelled upload {upload_id}")
            return True

        except OSError as e:
            logger.error(f"Failed to cancel upload {upload_id}: {e}")
            return False

    def cleanup_old_uploads(self, max_age_hours: int = 24) -> int:
        """Clean up uploads older than specified hours.

        Args:
            max_age_hours: Maximum age in hours before cleanup

        Returns:
            Number of uploads cleaned up
        """
        import time
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600

        cleaned = 0
        for state_file in self.STATE_DIR.glob("*.json"):
            try:
                mtime = state_file.stat().st_mtime
                if current_time - mtime > max_age_seconds:
                    upload_id = state_file.stem
                    self.cancel_upload(upload_id)
                    cleaned += 1
            except OSError:
                continue

        return cleaned

    def _save_state(self, state: ServerUploadState) -> None:
        """Persist upload state to disk.

        Args:
            state: State to persist
        """
        state_file = self.STATE_DIR / f"{state.upload_id}.json"
        try:
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state.to_dict(), f, indent=2)
        except OSError as e:
            logger.error(f"Failed to save upload state: {e}")

    def _get_extract_path(self, project_uuid: str, impression_uuid: str) -> str:
        """Get the extraction path for an upload.

        Args:
            project_uuid: Project identifier
            impression_uuid: Impression identifier

        Returns:
            Path to extract directory
        """
        return os.path.join(self.base_storage_path, project_uuid, impression_uuid)

    @staticmethod
    def _calculate_file_md5(file_path: str) -> str:
        """Calculate MD5 hash of a file.

        Args:
            file_path: Path to the file

        Returns:
            Hexadecimal MD5 hash string
        """
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()


# Global upload manager instance
_upload_manager: Optional[ResumableUploadManager] = None


def get_upload_manager(storage_path: str) -> ResumableUploadManager:
    """Get or create the global upload manager instance.

    Args:
        storage_path: Base path for Yuki storage

    Returns:
        ResumableUploadManager instance
    """
    global _upload_manager
    if _upload_manager is None:
        _upload_manager = ResumableUploadManager(storage_path)
    return _upload_manager
