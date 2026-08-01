"""
Neo4j Aura -- Graph Store (Layer 3, Sub-Phase 3)

Driver: neo4j 6.x
API:    session.execute_write() with UNWIND batch Cypher queries.

Design decisions:
  - All graph writes run inside a single managed transaction (execute_write),
    giving atomic commit/rollback and driver-level retry on transient errors.
  - Each node/relationship type is upserted via one UNWIND query, replacing the
    previous per-row tx.run() loop (N x M round-trips -> 6 fixed round-trips).
  - ON CREATE SET / ON MATCH SET are used instead of bare SET so immutable
    creation-time fields are not overwritten on re-upsert.
  - Every node type carries a `name` property so Neo4j Bloom can display labels
    inside circles without extra Perspective configuration.
  - Dynamic relationship type strings are embedded via f-string (Cypher does not
    support parameterised relationship types); all node/property values remain
    parameterised.
"""

from __future__ import annotations

import os
import uuid
from typing import List, Tuple

from neo4j import GraphDatabase

from remnant.config import settings


class Neo4jClientManager:
    """
    Thin wrapper around the Neo4j 6.x driver for graph upsert operations.

    A single driver instance is created at construction and reused for the
    lifetime of the application (the driver maintains an internal connection
    pool -- do not create a new instance per request).
    """

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        self.uri = uri or settings.remnant_neo4j_url
        self.user = user or settings.remnant_neo4j_username
        self.password = password or settings.remnant_neo4j_password
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self) -> None:
        """Release the driver's connection pool."""
        self.driver.close()

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    def upsert_memory_graph(
        self,
        memories: List,  # List[MemoryObject] -- avoid circular import
        relationships: List[Tuple[uuid.UUID, object, uuid.UUID]],
    ) -> None:
        """
        Persist a batch of MemoryObjects and their causal relationships to Neo4j
        inside a single managed, auto-retrying write transaction.

        Node types written:   Memory, Project, Session, Component, File
        Relationship types:   CONTAINS, PRODUCED, APPLIES_TO, TOUCHES
                              + causal edges from `relationships` parameter
                              (INFLUENCED, REJECTED_IN_FAVOR_OF, FIXES)
        """
        # Pre-process Python objects into plain dicts outside the transaction
        # function. Transaction functions must be idempotent and re-entryable
        # (the driver may retry them on transient errors), so all side-effect-
        # free data preparation belongs here.
        memory_rows, component_rows, file_rows, causal_rows = (
            self._build_batch_params(memories, relationships)
        )

        with self.driver.session(database="neo4j") as session:
            session.execute_write(
                self._write_graph,
                memory_rows,
                component_rows,
                file_rows,
                causal_rows,
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _project_display_name(project_id: str) -> str:
        """
        Derive a short human-readable name for a Project node from its ID.

        The project_id is typically a normalised Git remote URL such as
        'github.com/org/repo', so the last non-empty segment is the repo name.
        Falls back to the first 8 characters of the UUID if no segments exist.
        """
        parts = [p for p in project_id.replace("\\", "/").split("/") if p]
        return parts[-1] if parts else project_id[:8]

    @staticmethod
    def _build_batch_params(
        memories: List,
        relationships: List[Tuple[uuid.UUID, object, uuid.UUID]],
    ) -> tuple:
        """
        Convert Python MemoryObject instances into plain dicts suitable for
        Cypher parameterisation. Returns four lists:

          memory_rows    -- one dict per memory (covers Memory, Project, Session)
          component_rows -- one dict per (memory, component) pair
          file_rows      -- one dict per (memory, file_path) pair
          causal_rows    -- list of {rel_type, pairs} dicts for causal edges
        """
        memory_rows: list = []
        component_rows: list = []
        file_rows: list = []

        for mem in memories:
            mem_id = str(mem.id)
            project_id = str(mem.project_id)
            session_id = str(mem.session_id)
            memory_type = (
                mem.memory_type.value
                if hasattr(mem.memory_type, "value")
                else mem.memory_type
            )
            timestamp = mem.created_at.isoformat() if mem.created_at else None
            session_label = (
                mem.created_at.strftime("Session %Y-%m-%d %H:%M")
                if mem.created_at
                else session_id[:8]
            )

            memory_rows.append(
                {
                    "id": mem_id,
                    "name": mem.title,       # Bloom display label for Memory node
                    "title": mem.title,
                    "type": memory_type,
                    "project_id": project_id,
                    "project_name": Neo4jClientManager._project_display_name(project_id),
                    "session_id": session_id,
                    "session_name": session_label,  # Bloom display label for Session
                    "timestamp": timestamp,
                }
            )

            for comp in mem.components:
                component_rows.append(
                    {
                        "memory_id": mem_id,
                        "project_id": project_id,
                        "comp": comp,   # `name` is the MERGE key for Component
                    }
                )

            for fp in mem.file_paths:
                file_rows.append(
                    {
                        "memory_id": mem_id,
                        "project_id": project_id,
                        "path": fp,
                        "name": os.path.basename(fp) or fp,  # Bloom display label
                    }
                )

        # Group causal relationships by type so we can issue one UNWIND query
        # per distinct type.  Cypher does not support parameterised relationship
        # types, so we use f-strings for the type name only; all node identity
        # values remain parameterised.
        causal_by_type: dict = {}
        for source_id, rel_type, target_id in relationships:
            rel_str = (
                rel_type.value if hasattr(rel_type, "value") else str(rel_type)
            )
            causal_by_type.setdefault(rel_str, []).append(
                {"source_id": str(source_id), "target_id": str(target_id)}
            )

        causal_rows = [
            {"rel_type": rt, "pairs": pairs}
            for rt, pairs in causal_by_type.items()
        ]

        return memory_rows, component_rows, file_rows, causal_rows

    @staticmethod
    def _write_graph(
        tx,
        memory_rows: list,
        component_rows: list,
        file_rows: list,
        causal_rows: list,
    ) -> None:
        """
        Single transaction function executed by session.execute_write().

        Six targeted UNWIND queries replace the previous per-row tx.run() loop,
        reducing database round-trips from O(memories x node-types) to 6.

        ON CREATE SET  -- properties initialised only when a node is first created.
        ON MATCH SET   -- properties refreshed on every subsequent upsert.
        """
        if not memory_rows:
            return

        # -- 1. Memory nodes --------------------------------------------------
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (m:Memory {id: row.id})
            ON CREATE SET
                m.name       = row.name,
                m.title      = row.title,
                m.type       = row.type,
                m.project_id = row.project_id,
                m.session_id = row.session_id,
                m.timestamp  = row.timestamp
            ON MATCH SET
                m.name      = row.name,
                m.title     = row.title,
                m.type      = row.type,
                m.timestamp = row.timestamp
            """,
            rows=memory_rows,
        )

        # -- 2. Project nodes + CONTAINS edges --------------------------------
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (p:Project {id: row.project_id})
            ON CREATE SET p.name = row.project_name
            ON MATCH SET  p.name = row.project_name
            WITH p, row
            MERGE (m:Memory {id: row.id})
            MERGE (p)-[:CONTAINS]->(m)
            """,
            rows=memory_rows,
        )

        # -- 3. Session nodes + PRODUCED edges --------------------------------
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (s:Session {id: row.session_id})
            ON CREATE SET s.name = row.session_name
            ON MATCH SET  s.name = row.session_name
            WITH s, row
            MERGE (m:Memory {id: row.id})
            MERGE (s)-[:PRODUCED]->(m)
            """,
            rows=memory_rows,
        )

        # -- 4. Component nodes + APPLIES_TO edges ----------------------------
        if component_rows:
            tx.run(
                """
                UNWIND $rows AS row
                MERGE (c:Component {name: row.comp, project_id: row.project_id})
                WITH c, row
                MERGE (m:Memory {id: row.memory_id})
                MERGE (m)-[:APPLIES_TO]->(c)
                """,
                rows=component_rows,
            )

        # -- 5. File nodes + TOUCHES edges ------------------------------------
        if file_rows:
            tx.run(
                """
                UNWIND $rows AS row
                MERGE (f:File {path: row.path, project_id: row.project_id})
                ON CREATE SET f.name = row.name
                ON MATCH SET  f.name = row.name
                WITH f, row
                MERGE (m:Memory {id: row.memory_id})
                MERGE (m)-[:TOUCHES]->(f)
                """,
                rows=file_rows,
            )

        # -- 6. Causal Memory -> Memory edges ---------------------------------
        # One UNWIND query per distinct relationship type.  The type string is
        # embedded via f-string (unavoidable -- Cypher does not support
        # parameterised relationship types).  Node identity values stay
        # parameterised via $pairs.
        for group in causal_rows:
            rel_type: str = group["rel_type"]
            pairs: list = group["pairs"]
            tx.run(
                f"""
                UNWIND $pairs AS pair
                MERGE (s:Memory {{id: pair.source_id}})
                MERGE (t:Memory {{id: pair.target_id}})
                MERGE (s)-[:{rel_type}]->(t)
                """,
                pairs=pairs,
            )
