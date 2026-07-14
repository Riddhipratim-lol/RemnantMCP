import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import uuid

class PostgresStorage:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or os.getenv("REMNANT_DB_URL")
        
    def get_connection(self):
        if not self.db_url:
            raise ValueError("REMNANT_DB_URL environment variable is not set.")
        return psycopg2.connect(self.db_url)

    def get_or_create_project(self, project_id: str, name: str, repo_path: str) -> str:
        """
        Verify if a project exists, otherwise create it.
        Returns the project_id (UUID string).
        """
        # Ensure project_id is a valid UUID, otherwise generate/hash one
        val_id = self._normalize_uuid(project_id)
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM projects WHERE id = %s", (val_id,))
                    row = cur.fetchone()
                    if row:
                        return row[0]
                    
                    cur.execute(
                        "INSERT INTO projects (id, name, repo_path) VALUES (%s, %s, %s) RETURNING id",
                        (val_id, name, repo_path)
                    )
                    conn.commit()
                    return val_id
        except Exception as e:
            print(f"PostgreSQL storage error in get_or_create_project: {e}")
            return val_id

    def get_active_session(self, project_id: str, window_hours: int = 4) -> Optional[str]:
        """
        Query for an active session within the given window (in hours).
        Returns the session_id as string, or None if no active session is found.
        """
        proj_uuid = self._normalize_uuid(project_id)
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id FROM sessions 
                        WHERE project_id = %s AND status = 'ACTIVE' AND started_at >= %s
                        ORDER BY started_at DESC LIMIT 1
                        """,
                        (proj_uuid, cutoff_time)
                    )
                    row = cur.fetchone()
                    if row:
                        return row[0]
        except Exception as e:
            print(f"PostgreSQL storage error in get_active_session: {e}")
        return None

    def create_session(self, session_id: str, project_id: str) -> str:
        """
        Create a new session in the database.
        """
        sess_uuid = self._normalize_uuid(session_id)
        proj_uuid = self._normalize_uuid(project_id)
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO sessions (id, project_id, started_at, status) VALUES (%s, %s, %s, %s) RETURNING id",
                        (sess_uuid, proj_uuid, datetime.now(timezone.utc), 'ACTIVE')
                    )
                    conn.commit()
                    return sess_uuid
        except Exception as e:
            print(f"PostgreSQL storage error in create_session: {e}")
            return sess_uuid

    def is_hash_processed(self, project_id: str, content_hash: str) -> bool:
        """
        Check if a given content hash was already processed for the project.
        """
        proj_uuid = self._normalize_uuid(project_id)
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM session_log WHERE project_id = %s AND artifact_hash = %s LIMIT 1",
                        (proj_uuid, content_hash)
                    )
                    return cur.fetchone() is not None
        except Exception as e:
            print(f"PostgreSQL storage error in is_hash_processed: {e}")
            return False

    def log_artifact(self, project_id: str, session_id: str, artifact_hash: str, processed_sha: Optional[str] = None) -> None:
        """
        Log that an artifact hash has been processed.
        Also updates the session's artifact count.
        """
        proj_uuid = self._normalize_uuid(project_id)
        sess_uuid = self._normalize_uuid(session_id)
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Insert into session_log, ignoring duplicate hashes if they arise
                    cur.execute(
                        """
                        INSERT INTO session_log (project_id, session_id, processed_sha, artifact_hash)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (artifact_hash) DO NOTHING
                        """,
                        (proj_uuid, sess_uuid, processed_sha, artifact_hash)
                    )
                    # Increment artifact count in sessions table
                    cur.execute(
                        "UPDATE sessions SET artifact_count = artifact_count + 1 WHERE id = %s",
                        (sess_uuid,)
                    )
                    conn.commit()
        except Exception as e:
            print(f"PostgreSQL storage error in log_artifact: {e}")

    def get_last_processed_sha(self, project_id: str) -> Optional[str]:
        """
        Get the most recently processed commit SHA for this project.
        """
        proj_uuid = self._normalize_uuid(project_id)
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT processed_sha FROM session_log 
                        WHERE project_id = %s AND processed_sha IS NOT NULL 
                        ORDER BY processed_at DESC LIMIT 1
                        """
                        , (proj_uuid,)
                    )
                    row = cur.fetchone()
                    if row:
                        return row[0]
        except Exception as e:
            print(f"PostgreSQL storage error in get_last_processed_sha: {e}")
        return None

    def _normalize_uuid(self, val: str) -> str:
        """
        Ensures the input value is a valid UUID format. 
        If it's already a valid UUID, returns it. Otherwise, generates a deterministic UUID.
        """
        if not val:
            return str(uuid.uuid4())
        try:
            uuid.UUID(val)
            return val
        except ValueError:
            # Deterministic namespace UUID from a string (e.g. repo URL)
            return str(uuid.uuid5(uuid.NAMESPACE_DNS, val))
