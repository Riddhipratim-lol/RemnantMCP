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

        Strategy (in order of preference):
          1. tsvector ranking with websearch_to_tsquery for natural multi-word queries.
          2. If tsvector returns 0 results, automatically try ILIKE pattern search.
          3. If ILIKE also fails (exception), return empty list.

        This layered approach ensures recall_context is never silently empty
        when memories exist for the project.

        Args:
            query:       The user query text.
            project_id:  Project scope for isolation.
            top_k:       Max results to return.
            memory_type: Optional memory type filter string.

        Returns:
            List of MemoryObject instances ordered by relevance.
        """
        try:
            results = self._ts_search(query, project_id, top_k, memory_type)
            if results:
                return results
            # tsvector found nothing — fall through to ILIKE for broader matching
            return self._ilike_search(query, project_id, top_k, memory_type)
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

        The tsvector expression MUST match the GIN index definition in schema.sql
        exactly (title + content + rationale) so the index is used efficiently.
        """
        proj_uuid = self.pg._normalize_uuid(project_id)

        # plainto_tsquery requires at least one lexeme; single characters or
        # very short strings produce an empty tsquery that matches nothing.
        # In that case, fall back directly to the ILIKE search.
        if len(query.strip()) < 2:
            return self._ilike_search(query, project_id, top_k, memory_type)

        base_sql = """
            SELECT
                id, project_id, session_id, memory_type,
                title, content, rationale,
                components, file_paths, tags,
                confidence_score, created_at, updated_at,
                is_superseded, superseded_by,
                ts_rank(
                    to_tsvector('english',
                        coalesce(title, '') || ' ' ||
                        coalesce(content, '') || ' ' ||
                        coalesce(rationale, '')
                    ),
                    plainto_tsquery('english', %s)
                ) AS rank
            FROM memories
            WHERE
                project_id = %s
                AND is_superseded = FALSE
                AND to_tsvector('english',
                        coalesce(title, '') || ' ' ||
                        coalesce(content, '') || ' ' ||
                        coalesce(rationale, '')
                    ) @@ plainto_tsquery('english', %s)
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
        Pattern-matching fallback using ILIKE (case-insensitive LIKE).

        Splits the query into individual keywords and uses OR logic so that
        ANY keyword match retrieves the memory. This is deliberately broad —
        it is a last-resort fallback to ensure memories are surfaced even
        when tsvector produces no results (e.g. short stop-words, lexeme misses).

        Also serves as a broad "return recent memories" fallback when the query
        is very short (single character, empty, or a stop-word that FTS drops).
        """
        proj_uuid = self.pg._normalize_uuid(project_id)

        # For very short / empty queries, return most recent active memories
        # rather than searching — ensures recall_context is never empty when
        # memories exist for the project.
        query_stripped = query.strip()
        if not query_stripped or len(query_stripped) < 2:
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
            """
            params: list = [proj_uuid]
            if memory_type:
                base_sql += " AND memory_type = %s"
                params.append(memory_type)
            base_sql += " ORDER BY created_at DESC LIMIT %s"
            params.append(top_k)
            return self._execute_and_map(base_sql, params)

        # Split into keywords (2+ chars), deduplicate, limit to 10 tokens
        # to keep the query reasonable.
        tokens = list(dict.fromkeys(
            t for t in query_stripped.split() if len(t) >= 2
        ))[:10]

        if not tokens:
            # Fallback: treat the whole query as one pattern
            tokens = [query_stripped]

        # Build: (title ILIKE %tok1% OR content ILIKE %tok1% OR rationale ILIKE %tok1%)
        #    OR  (title ILIKE %tok2% OR content ILIKE %tok2% OR rationale ILIKE %tok2%)
        # Using OR across tokens so ANY keyword is enough for a match.
        token_clauses = []
        params = [proj_uuid]
        for tok in tokens:
            pattern = f"%{tok}%"
            token_clauses.append(
                "(title ILIKE %s OR content ILIKE %s OR rationale ILIKE %s)"
            )
            params.extend([pattern, pattern, pattern])

        where_tokens = " OR ".join(token_clauses)

        base_sql = f"""
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
                AND ({where_tokens})
        """

        if memory_type:
            base_sql += " AND memory_type = %s"

        base_sql += " ORDER BY confidence_score DESC LIMIT %s"

        # Build final param list matching the SQL placeholder order:
        #   1. project_id  (for project_id = %s)
        #   2. token patterns  (3 per token: title, content, rationale)
        #   3. memory_type  (optional)
        #   4. top_k  (for LIMIT %s)
        final_params: list = [proj_uuid]
        for tok in tokens:
            pat = f"%{tok}%"
            final_params.extend([pat, pat, pat])
        if memory_type:
            final_params.append(memory_type)
        final_params.append(top_k)

        return self._execute_and_map(base_sql, final_params)


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
