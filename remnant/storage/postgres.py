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

    def insert_memory_batch(self, memories: List['MemoryObject'], relationships: List[Tuple[uuid.UUID, 'RelationshipType', uuid.UUID]]) -> None:
        """
        Inserts a batch of memories and their relationships within a single transaction.
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    for mem in memories:
                        cur.execute(
                            """
                            INSERT INTO memories (
                                id, project_id, session_id, memory_type, title, content, 
                                rationale, components, file_paths, tags, confidence_score, 
                                created_at, updated_at, is_superseded, superseded_by
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE SET
                                title = EXCLUDED.title,
                                content = EXCLUDED.content,
                                rationale = EXCLUDED.rationale,
                                components = EXCLUDED.components,
                                file_paths = EXCLUDED.file_paths,
                                tags = EXCLUDED.tags,
                                confidence_score = EXCLUDED.confidence_score,
                                updated_at = EXCLUDED.updated_at
                            """,
                            (
                                str(mem.id), str(mem.project_id), str(mem.session_id), 
                                mem.memory_type.value if hasattr(mem.memory_type, 'value') else mem.memory_type,
                                mem.title, mem.content, mem.rationale, 
                                mem.components, mem.file_paths, mem.tags, mem.confidence_score,
                                mem.created_at, mem.updated_at, mem.is_superseded, 
                                str(mem.superseded_by) if mem.superseded_by else None
                            )
                        )
                    
                    for rel in relationships:
                        source_id, rel_type, target_id = rel
                        # Create a deterministic UUID for the relationship based on source, target, type
                        rel_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{source_id}_{rel_type}_{target_id}"))
                        cur.execute(
                            """
                            INSERT INTO memory_relationships (id, source_memory_id, target_memory_id, relationship_type)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT ON CONSTRAINT unique_relationship DO NOTHING
                            """,
                            (
                                rel_id, str(source_id), str(target_id), 
                                rel_type.value if hasattr(rel_type, 'value') else rel_type
                            )
                        )
                conn.commit()
        except Exception as e:
            print(f"PostgreSQL storage error in insert_memory_batch: {e}")
            raise e  # Re-raise to trigger rollback logic in fan-out writer

    def mark_superseded(self, memory_id: str, superseded_by: Optional[str] = None) -> bool:
        """
        Marks a memory as superseded, optionally pointing to the new memory ID.
        """
        mem_uuid = self._normalize_uuid(memory_id)
        sup_uuid = self._normalize_uuid(superseded_by) if superseded_by else None
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE memories 
                        SET is_superseded = TRUE, superseded_by = %s, updated_at = %s
                        WHERE id = %s
                        """,
                        (sup_uuid, datetime.now(timezone.utc), mem_uuid)
                    )
                    conn.commit()
                    return cur.rowcount > 0
        except Exception as e:
            print(f"PostgreSQL storage error in mark_superseded: {e}")
            return False
