"""
Layer 4 — Phase 5: Search Fallback Handler

When Qdrant or Neo4j are unreachable, this module provides a
PostgreSQL full-text search fallback using tsvector/tsquery.
It queries the `memories` table with a GIN-indexed ts_rank
against the `title` and `content` columns.

This is the safety net referenced in:
  - Implementation.md §4 "Search Fallback Handler"
  - Project_Vision.md §15 "Failure Modes & Resilience"
"""

import os
from typing import List, Optional
import uuid

from remnant.storage.postgres import PostgresStorage
from remnant.structures import MemoryObject, MemoryType


class PostgresFallbackSearch:
    """
    Full-text search fallback using PostgreSQL tsvector/tsquery.

    Falls back to a simple ILIKE search when tsvector is not available
    (e.g. test environments without the pg_catalog text search config).

    Args:
        db_url: Overrides REMNANT_DB_URL env variable.
    """

    def __init__(self, db_url: Optional[str] = None):
        self.pg = PostgresStorage(db_url)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        project_id: str,
        top_k: int = 20,
        memory_type: Optional[str] = None,
    ) -> List[MemoryObject]:
        """
        Execute a full-text search query against PostgreSQL.

        First attempts to use tsvector ranking (GIN index).
        If the tsquery parse fails (e.g. unsupported syntax), falls back
        to a simple ILIKE pattern search.

        Args:
            query:       The user query text.
            project_id:  Project scope for isolation.
            top_k:       Max results to return.
            memory_type: Optional memory type filter string.

        Returns:
            List of MemoryObject instances ordered by relevance.
        """
        try:
            return self._ts_search(query, project_id, top_k, memory_type)
        except Exception as exc:
            print(f"[FallbackSearch] tsvector search failed ({exc}), trying ILIKE.")
            try:
                return self._ilike_search(query, project_id, top_k, memory_type)
            except Exception as exc2:
                print(f"[FallbackSearch] ILIKE search also failed: {exc2}")
                return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ts_search(
        self,
        query: str,
        project_id: str,
        top_k: int,
        memory_type: Optional[str],
    ) -> List[MemoryObject]:
        """
        PostgreSQL tsvector full-text search with ts_rank ordering.
        Uses plainto_tsquery for safe tokenisation of arbitrary user input.
        """
        proj_uuid = self.pg._normalize_uuid(project_id)

        base_sql = """
            SELECT
                id, project_id, session_id, memory_type,
                title, content, rationale,
                components, file_paths, tags,
                confidence_score, created_at, updated_at,
                is_superseded, superseded_by,
                ts_rank(
                    to_tsvector('english', coalesce(title,'') || ' ' || coalesce(content,'')),
                    plainto_tsquery('english', %s)
                ) AS rank
            FROM memories
            WHERE
                project_id = %s
                AND is_superseded = FALSE
                AND to_tsvector('english', coalesce(title,'') || ' ' || coalesce(content,''))
                    @@ plainto_tsquery('english', %s)
        """
        params: list = [query, proj_uuid, query]

        if memory_type:
            base_sql += " AND memory_type = %s"
            params.append(memory_type)

        base_sql += " ORDER BY rank DESC LIMIT %s"
        params.append(top_k)

        return self._execute_and_map(base_sql, params)

    def _ilike_search(
        self,
        query: str,
        project_id: str,
        top_k: int,
        memory_type: Optional[str],
    ) -> List[MemoryObject]:
        """
        Simple pattern-matching fallback using ILIKE (case-insensitive LIKE).
        Searches title and content columns.
        """
        proj_uuid = self.pg._normalize_uuid(project_id)
        pattern = f"%{query}%"

        base_sql = """
            SELECT
                id, project_id, session_id, memory_type,
                title, content, rationale,
                components, file_paths, tags,
                confidence_score, created_at, updated_at,
                is_superseded, superseded_by
            FROM memories
            WHERE
                project_id = %s
                AND is_superseded = FALSE
                AND (title ILIKE %s OR content ILIKE %s)
        """
        params: list = [proj_uuid, pattern, pattern]

        if memory_type:
            base_sql += " AND memory_type = %s"
            params.append(memory_type)

        base_sql += " ORDER BY confidence_score DESC LIMIT %s"
        params.append(top_k)

        return self._execute_and_map(base_sql, params)

    def _execute_and_map(self, sql: str, params: list) -> List[MemoryObject]:
        """
        Execute a SQL query and map each row to a MemoryObject.
        """
        memories: List[MemoryObject] = []
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            with self.pg.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()

            for row in rows:
                memories.append(
                    MemoryObject(
                        id=uuid.UUID(str(row["id"])),
                        project_id=uuid.UUID(str(row["project_id"])),
                        session_id=uuid.UUID(str(row["session_id"])),
                        memory_type=MemoryType(row["memory_type"]),
                        title=row["title"] or "",
                        content=row["content"] or "",
                        rationale=row["rationale"],
                        components=list(row["components"] or []),
                        file_paths=list(row["file_paths"] or []),
                        tags=list(row["tags"] or []),
                        confidence_score=float(row["confidence_score"] or 1.0),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        is_superseded=bool(row["is_superseded"]),
                        superseded_by=(
                            uuid.UUID(str(row["superseded_by"]))
                            if row["superseded_by"]
                            else None
                        ),
                    )
                )
        except Exception as exc:
            print(f"[FallbackSearch] DB execute error: {exc}")
            raise

        return memories
