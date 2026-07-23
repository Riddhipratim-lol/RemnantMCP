import hashlib
from datetime import datetime, timezone
import uuid
from typing import Dict, List, Tuple, Optional
from remnant.structures import ArtifactObject, SourceType
from remnant.storage.postgres import PostgresStorage

def calculate_sha256(text: str) -> str:
    """Calculate the SHA-256 hash of a text block."""
    if not text:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

class SessionGrouper:
    def __init__(self, db_storage: Optional[PostgresStorage] = None, window_hours: int = 4):
        self.db_storage = db_storage or PostgresStorage()
        self.window_hours = window_hours

    def process_and_group(
        self,
        raw_artifacts: List[Dict],
        project_id: str,
        commit_sha: Optional[str] = None
    ) -> Tuple[str, List[ArtifactObject]]:
        """
        Groups raw artifacts under a session, deduplicates them using SHA-256 content hashes,
        and saves/logs them to the database.
        
        Args:
            raw_artifacts: List of dicts representing raw artifacts with fields:
                - 'source_type': SourceType or str
                - 'raw_content': str
                - 'file_paths': List[str] (optional)
                - 'metadata': Dict (optional)
                - 'timestamp': datetime (optional)
            project_id: The project UUID or identifier string.
            commit_sha: Optional current git commit SHA associated with the session.
            
        Returns:
            session_id (str): The active or newly created session UUID string.
            final_artifacts (List[ArtifactObject]): Deduplicated and session-grouped artifacts.
        """
        # Normalize to a valid UUID string.
        # The coordinator (or caller) is responsible for ensuring the project already exists
        # in the DB. We avoid a redundant get_or_create_project call here to prevent
        # repo_path overwrite with os.getcwd() fallback.
        project_uuid = self.db_storage._normalize_uuid(project_id)
        
        # 1. Resolve or create active session
        session_id = self.db_storage.get_active_session(project_uuid, self.window_hours)
        if not session_id:
            session_id = str(uuid.uuid4())
            session_id = self.db_storage.create_session(session_id, project_uuid)
            
        final_artifacts = []
        
        for raw in raw_artifacts:
            raw_content = raw.get("raw_content", "")
            if not raw_content.strip():
                continue
                
            content_hash = calculate_sha256(raw_content)
            
            # 2. Check deduplication
            if self.db_storage.is_hash_processed(project_uuid, content_hash):
                # Skip duplicate
                continue
                
            source_type = raw.get("source_type")
            if isinstance(source_type, str):
                try:
                    source_type = SourceType(source_type)
                except ValueError:
                    source_type = SourceType.CHAT # default fallback
                    
            timestamp = raw.get("timestamp")
            if not timestamp:
                timestamp = datetime.now(timezone.utc)
                
            artifact = ArtifactObject(
                source_type=source_type,
                project_id=project_uuid,
                session_id=session_id,
                timestamp=timestamp,
                raw_content=raw_content,
                file_paths=raw.get("file_paths", []),
                metadata=raw.get("metadata", {}),
                content_hash=content_hash
            )
            
            # 3. Log the processed hash to the database session log
            self.db_storage.log_artifact(
                project_id=project_uuid,
                session_id=session_id,
                artifact_hash=content_hash,
                processed_sha=commit_sha if source_type == SourceType.GIT_DIFF or source_type == SourceType.COMMIT else None
            )
            
            final_artifacts.append(artifact)
            
        return session_id, final_artifacts
