"""
Layer 4 — Phase 2: Graph Expansion (Neo4j)

Takes the initial semantic candidate set from Qdrant, traverses the
knowledge graph 1-2 hops, and merges causally-related memories
(failed approaches, constraints, influenced decisions) into the
candidate pool.  Full MemoryObject payloads are resolved from
PostgreSQL for any new IDs discovered through graph traversal.
"""

from remnant.config import settings
from typing import List, Optional
import uuid

from neo4j import GraphDatabase

from remnant.storage.postgres import PostgresStorage
from remnant.structures import MemoryObject


class GraphExpansion:
    """
    Expands a set of seed memory IDs via Neo4j relationship traversal.

    Relationship types traversed (1-2 hops, bidirectional):
      - INFLUENCED
      - REJECTED_IN_FAVOR_OF
      - FIXES
    """

    # Relationship types that represent causal relevance
    CAUSAL_RELS = ["INFLUENCED", "REJECTED_IN_FAVOR_OF", "FIXES"]

    def __init__(
        self,
        neo4j_uri: Optional[str] = None,
        neo4j_user: Optional[str] = None,
        neo4j_password: Optional[str] = None,
        db_url: Optional[str] = None,
    ):
        self.neo4j_uri = neo4j_uri or settings.remnant_neo4j_url
        self.neo4j_user = neo4j_user or settings.remnant_neo4j_username
        self.neo4j_password = neo4j_password or settings.remnant_neo4j_password
        self.driver = GraphDatabase.driver(
            self.neo4j_uri, auth=(self.neo4j_user, self.neo4j_password)
        )
        self.pg = PostgresStorage(db_url)

    def close(self) -> None:
        """Release Neo4j driver resources."""
        self.driver.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def expand(
        self,
        seed_memory_ids: List[str],
        project_id: str,
    ) -> List[MemoryObject]:
        """
        Phase 2 entry point: traverse graph from seeds, fetch full
        MemoryObject payloads from PostgreSQL, return the merged set.

        Args:
            seed_memory_ids: Memory UUIDs returned by Phase 1 (Qdrant).
            project_id:      Used to scope PostgreSQL look-ups.

        Returns:
            Deduplicated list of MemoryObject instances (seeds +
            causally related neighbours).
        """
        if not seed_memory_ids:
            return []

        # 1. Discover related IDs through graph traversal
        related_ids = self._traverse(seed_memory_ids)

        # 2. Merge seeds + related, maintaining insertion order for ranking hints
        all_ids = list(dict.fromkeys(seed_memory_ids + related_ids))

        # 3. Resolve full MemoryObjects from PostgreSQL
        memories = self._fetch_memories(all_ids, project_id)
        return memories

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _traverse(self, seed_ids: List[str]) -> List[str]:
        """
        Run a 1-2 hop Cypher traversal over causal relationship types and
        return the IDs of all neighbour Memory nodes discovered.
        """
        if not seed_ids:
            return []

        rel_pattern = "|".join(self.CAUSAL_RELS)
        cypher = f"""
        MATCH (seed:Memory)
        WHERE seed.id IN $seed_ids
        MATCH (seed)-[:{rel_pattern}*1..2]-(related:Memory)
        WHERE related.id <> seed.id
        RETURN DISTINCT related.id AS related_id
        """
        try:
            records, _, _ = self.driver.execute_query(
                cypher,
                seed_ids=seed_ids,
                database_=settings.remnant_neo4j_database or None,
            )
            return [r["related_id"] for r in records if r["related_id"]]
        except Exception as exc:
            print(f"[GraphExpansion] Neo4j traversal error: {exc}")
            return []

    def _fetch_memories(
        self,
        memory_ids: List[str],
        project_id: str,
    ) -> List[MemoryObject]:
        """
        Resolve full MemoryObject records from PostgreSQL for the given IDs.
        IDs that don't exist in the DB (e.g. graph nodes without a PG record)
        are silently skipped.

        Args:
            memory_ids:  List of memory UUID strings to look up.
            project_id:  For scoped error logging only; PG uses id as PK.

        Returns:
            Ordered list of MemoryObject instances.
        """
        if not memory_ids:
            return []

        memories: List[MemoryObject] = []
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            with self.pg.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT
                            id, project_id, session_id, memory_type,
                            title, content, rationale,
                            components, file_paths, tags,
                            confidence_score, created_at, updated_at,
                            is_superseded, superseded_by
                        FROM memories
                        WHERE id = ANY(%s)
                        ORDER BY array_position(%s, id::text)
                        """,
                        (memory_ids, memory_ids),
                    )
                    rows = cur.fetchall()

            for row in rows:
                from remnant.structures import MemoryType
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
            print(f"[GraphExpansion] PostgreSQL fetch error: {exc}")

        return memories
