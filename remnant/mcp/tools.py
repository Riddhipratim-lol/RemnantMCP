"""
Layer 5 — MCP Tool Implementations

Implements the five MCP tools exposed by the RemnantMCP server:

    1. remember_session     — Ingest session artifacts → Layer 1 + 2 pipeline
    2. recall_context       — Hybrid retrieval → Layer 4 pipeline
    3. list_decisions       — List ARCHITECTURAL_DECISION memories
    4. get_failed_approaches — List FAILED_APPROACH memories
    5. mark_superseded      — Soft-deprecate a memory record

Each function is a plain Python callable decorated by the MCP server in
`mcp_server.py`. Keeping tool logic here (separate from the server entrypoint)
makes unit testing straightforward without spinning up a live MCP server.
"""

import os
from remnant.config import settings
import time
from typing import Any, Dict, List, Optional

from remnant.agent.graph import build_graph
from remnant.ingestion.coordinator import IngestionCoordinator
from remnant.mcp.audit import AuditLogger
from remnant.mcp.project import ProjectDetector
from remnant.retrieval.coordinator import RetrievalCoordinator
from remnant.storage.postgres import PostgresStorage
from remnant.structures import MemoryType


# ---------------------------------------------------------------------------
# Shared singletons (initialised lazily on first use)
# ---------------------------------------------------------------------------

_pg: Optional[PostgresStorage] = None
_audit: Optional[AuditLogger] = None
_project_detector: Optional[ProjectDetector] = None
_retrieval_coordinator: Optional[RetrievalCoordinator] = None


def _get_pg() -> PostgresStorage:
    global _pg
    if _pg is None:
        _pg = PostgresStorage()
    return _pg


def _get_audit() -> AuditLogger:
    global _audit
    if _audit is None:
        _audit = AuditLogger(db_storage=_get_pg())
    return _audit


def _get_project_detector() -> ProjectDetector:
    global _project_detector
    if _project_detector is None:
        _project_detector = ProjectDetector(db_storage=_get_pg())
    return _project_detector


def _get_retrieval_coordinator() -> RetrievalCoordinator:
    global _retrieval_coordinator
    if _retrieval_coordinator is None:
        _retrieval_coordinator = RetrievalCoordinator(
            qdrant_url=settings.remnant_qdrant_url,
            qdrant_api_key=settings.remnant_qdrant_api_key,
            neo4j_uri=settings.remnant_neo4j_url,
            neo4j_user=settings.remnant_neo4j_username,
            neo4j_password=settings.remnant_neo4j_password,
            voyage_api_key=settings.voyage_api_key,
            db_url=settings.remnant_db_url,
        )
    return _retrieval_coordinator


# ---------------------------------------------------------------------------
# Tool 1: remember_session
# ---------------------------------------------------------------------------

def remember_session(
    project_id: Optional[str] = None,
    chat_transcript: Optional[str] = None,
    commit_sha: Optional[str] = None,
    session_notes: Optional[str] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ingest and extract memories from a completed coding session.

    Triggers:
      Layer 1 (Ingestion Pipeline) → parses git diffs, commit messages,
      chat transcript, and error logs into ArtifactObjects.

      Layer 2 (LangGraph Extraction Agent) → classifies, extracts, resolves
      entities, maps relationships, validates, and writes MemoryObjects to all
      three stores (PostgreSQL, Qdrant, Neo4j).

    Args:
        project_id:      Optional explicit project UUID. If omitted the server
                         auto-detects from the git remote URL.
        chat_transcript: Raw chat/session transcript text (optional).
        commit_sha:      Target commit SHA. Defaults to HEAD.
        session_notes:   Free-form developer notes for the session (optional).
        project_root:    Workspace directory path. Defaults to
                         REMNANT_PROJECT_ROOT env var or cwd.

    Returns:
        {
            "memories_extracted": int,
            "session_id": str,
            "summary": str,
            "storage": { "postgres": bool, "qdrant": bool, "neo4j": bool }
        }
    """
    t0 = time.monotonic()
    root = project_root or settings.remnant_project_root or os.getcwd()
    input_params = {
        "project_id": project_id,
        "commit_sha": commit_sha,
        "chat_transcript": chat_transcript,
        "session_notes": session_notes,
        "project_root": root,
    }

    # Resolve project
    resolved_project_id = project_id or _get_project_detector().resolve(root)

    try:
        # ---- Layer 1: Ingestion ----
        coordinator = IngestionCoordinator(db_storage=_get_pg())
        session_id, artifacts = coordinator.ingest_session(
            repo_path=root,
            project_id=resolved_project_id,
            chat_transcript=chat_transcript,
            session_notes=session_notes,
            commit_sha=commit_sha,
        )

        if not artifacts:
            duration = int((time.monotonic() - t0) * 1000)
            summary = "No new artifacts detected (all content was previously ingested or repository is empty)."
            _get_audit().log(
                tool_name="remember_session",
                project_id=resolved_project_id,
                input_params=input_params,
                output_summary=summary,
                duration_ms=duration,
            )
            return {
                "memories_extracted": 0,
                "session_id": session_id,
                "summary": summary,
                "storage": {"postgres": False, "qdrant": False, "neo4j": False},
            }

        # ---- Layer 2: Knowledge Extraction (LangGraph) ----
        graph = build_graph()
        initial_state = {
            "session_id": session_id,
            "project_id": resolved_project_id,
            "project_root": root,
            "artifacts": artifacts,
            "classified_artifacts": [],
            "raw_memories": [],
            "resolved_memories": [],
            "relationships": [],
            "validation_errors": [],
            "retry_count": 0,
            "final_memories": [],
            "storage_results": {},
        }
        final_state = graph.invoke(initial_state)

        storage_results: Dict[str, Any] = final_state.get("storage_results", {})
        memories_written: int = storage_results.get("memories_written", 0)
        errors: List[str] = storage_results.get("errors", [])

        summary = (
            f"Extracted {memories_written} memory/memories from "
            f"{len(artifacts)} artifact(s) across session {session_id}."
        )
        if errors:
            summary += f" Storage warnings: {'; '.join(errors)}"

        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log(
            tool_name="remember_session",
            project_id=resolved_project_id,
            input_params=input_params,
            output_summary=summary,
            duration_ms=duration,
        )

        return {
            "memories_extracted": memories_written,
            "session_id": session_id,
            "summary": summary,
            "storage": {
                "postgres": storage_results.get("postgres", False),
                "qdrant": storage_results.get("qdrant", False),
                "neo4j": storage_results.get("neo4j", False),
            },
        }

    except Exception as exc:
        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log_error(
            tool_name="remember_session",
            project_id=resolved_project_id,
            input_params=input_params,
            exc=exc,
            duration_ms=duration,
        )
        raise


# ---------------------------------------------------------------------------
# Tool 2: recall_context
# ---------------------------------------------------------------------------

def recall_context(
    query: str,
    project_id: Optional[str] = None,
    component: Optional[str] = None,
    file_path: Optional[str] = None,
    memory_types: Optional[List[str]] = None,
    max_tokens: int = 2000,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retrieve the most relevant memories for the current task context.

    Triggers the full three-phase retrieval pipeline (Layer 4):
      Phase 1 — Semantic search (Qdrant + Voyage Code 3)
      Phase 2 — Graph expansion (Neo4j causal traversal)
      Phase 3 — Re-ranking (Voyage rerank-2.5-lite)
      Phase 4 — Context packing (token-budget-aware formatting)

    Args:
        query:        Current task description, code snippet, or question.
        project_id:   Optional explicit project UUID; auto-detected if omitted.
        component:    Optional component name to scope retrieval (e.g. "auth-service").
        file_path:    Optional file path to scope retrieval.
        memory_types: Optional list of MemoryType strings to filter
                      (e.g. ["ARCHITECTURAL_DECISION", "FAILED_APPROACH"]).
        max_tokens:   Token budget for the returned context block (default: 2000).
        project_root: Workspace directory path for auto-detection.

    Returns:
        {
            "context_block": str,    # Formatted memory context ready for injection
            "memory_ids": List[str], # UUIDs of memories included
            "retrieved_count": int,  # Total memories evaluated
            "fallback_used": bool    # True when vector/graph stores were offline
        }
    """
    t0 = time.monotonic()
    root = project_root or settings.remnant_project_root or os.getcwd()
    input_params = {
        "query": query,
        "project_id": project_id,
        "component": component,
        "file_path": file_path,
        "memory_types": memory_types,
        "max_tokens": max_tokens,
    }

    resolved_project_id = project_id or _get_project_detector().resolve(root)

    try:
        result = _get_retrieval_coordinator().retrieve(
            query=query,
            project_id=resolved_project_id,
            component=component,
            file_path=file_path,
            memory_types=memory_types,
            max_tokens=max_tokens,
        )

        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log(
            tool_name="recall_context",
            project_id=resolved_project_id,
            input_params=input_params,
            output_summary=(
                f"Retrieved {result.retrieved_count} memories; "
                f"fallback={result.fallback_used}"
            ),
            duration_ms=duration,
        )

        return {
            "context_block": result.context_block,
            "memory_ids": result.memory_ids,
            "retrieved_count": result.retrieved_count,
            "fallback_used": result.fallback_used,
        }

    except Exception as exc:
        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log_error(
            tool_name="recall_context",
            project_id=resolved_project_id,
            input_params=input_params,
            exc=exc,
            duration_ms=duration,
        )
        raise


# ---------------------------------------------------------------------------
# Tool 3: list_decisions
# ---------------------------------------------------------------------------

def list_decisions(
    project_id: Optional[str] = None,
    component: Optional[str] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return all architectural decisions stored for a project.

    Queries PostgreSQL directly for memories of type ARCHITECTURAL_DECISION.
    Optionally scoped to a specific component.

    Args:
        project_id:   Optional explicit project UUID; auto-detected if omitted.
        component:    Optional component name to filter results.
        project_root: Workspace directory path for auto-detection.

    Returns:
        {
            "decisions": [
                {
                    "id": str,
                    "title": str,
                    "rationale": str,
                    "date": str,       # ISO-8601
                    "files": List[str],
                    "components": List[str],
                    "tags": List[str],
                    "is_superseded": bool,
                }
            ],
            "total": int
        }
    """
    t0 = time.monotonic()
    root = project_root or settings.remnant_project_root or os.getcwd()
    input_params = {"project_id": project_id, "component": component}

    resolved_project_id = project_id or _get_project_detector().resolve(root)

    try:
        decisions = _query_memories_by_type(
            project_id=resolved_project_id,
            memory_type=MemoryType.ARCHITECTURAL_DECISION,
            component=component,
        )

        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log(
            tool_name="list_decisions",
            project_id=resolved_project_id,
            input_params=input_params,
            output_summary=f"Returned {len(decisions)} decisions.",
            duration_ms=duration,
        )

        return {"decisions": decisions, "total": len(decisions)}

    except Exception as exc:
        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log_error("list_decisions", resolved_project_id, input_params, exc, duration)
        raise


# ---------------------------------------------------------------------------
# Tool 4: get_failed_approaches
# ---------------------------------------------------------------------------

def get_failed_approaches(
    query: str,
    project_id: Optional[str] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retrieve failed approaches to prevent repeating previously-tried solutions.

    Combines a targeted PostgreSQL query (FAILED_APPROACH type) with optional
    semantic similarity on the query string for ranking.

    Args:
        query:        Task description or topic area to search.
        project_id:   Optional explicit project UUID; auto-detected if omitted.
        project_root: Workspace directory path for auto-detection.

    Returns:
        {
            "failed_approaches": [
                {
                    "id": str,
                    "title": str,
                    "what_was_tried": str,    # content field
                    "why_abandoned": str,     # rationale field
                    "date": str,              # ISO-8601 created_at
                    "files": List[str],
                    "components": List[str],
                }
            ],
            "total": int
        }
    """
    t0 = time.monotonic()
    root = project_root or settings.remnant_project_root or os.getcwd()
    input_params = {"query": query, "project_id": project_id}

    resolved_project_id = project_id or _get_project_detector().resolve(root)

    try:
        rows = _query_memories_by_type(
            project_id=resolved_project_id,
            memory_type=MemoryType.FAILED_APPROACH,
            component=None,
        )

        failed = [
            {
                "id": r["id"],
                "title": r["title"],
                "what_was_tried": r.get("content", ""),
                "why_abandoned": r.get("rationale", ""),
                "date": r.get("date"),
                "files": r.get("files", []),
                "components": r.get("components", []),
            }
            for r in rows
        ]

        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log(
            tool_name="get_failed_approaches",
            project_id=resolved_project_id,
            input_params=input_params,
            output_summary=f"Returned {len(failed)} failed approaches.",
            duration_ms=duration,
        )

        return {"failed_approaches": failed, "total": len(failed)}

    except Exception as exc:
        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log_error("get_failed_approaches", resolved_project_id, input_params, exc, duration)
        raise


# ---------------------------------------------------------------------------
# Tool 5: mark_superseded
# ---------------------------------------------------------------------------

def mark_superseded(
    memory_id: str,
    reason: str,
    new_memory_id: Optional[str] = None,
    project_root: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Soft-deprecate an outdated memory by marking it as superseded.

    Updates the `is_superseded` flag and optional `superseded_by` FK on the
    PostgreSQL memories table, preserving historical provenance.

    Args:
        memory_id:     UUID of the memory to deprecate.
        reason:        Human-readable explanation of why it is superseded.
        new_memory_id: UUID of the replacement memory, if known (optional).
        project_root:  Workspace directory path for project auto-detection.

    Returns:
        {
            "success": bool,
            "memory_id": str,
            "new_memory_id": str | None,
            "message": str
        }
    """
    t0 = time.monotonic()
    input_params = {
        "memory_id": memory_id,
        "reason": reason,
        "new_memory_id": new_memory_id,
    }

    try:
        success = _get_pg().mark_superseded(
            memory_id=memory_id,
            superseded_by=new_memory_id,
        )

        message = (
            f"Memory {memory_id} successfully marked as superseded. Reason: {reason}"
            if success
            else f"No memory found with id={memory_id}."
        )

        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log(
            tool_name="mark_superseded",
            project_id=None,
            input_params=input_params,
            output_summary=message,
            status="success" if success else "not_found",
            duration_ms=duration,
        )

        return {
            "success": success,
            "memory_id": memory_id,
            "new_memory_id": new_memory_id,
            "message": message,
        }

    except Exception as exc:
        duration = int((time.monotonic() - t0) * 1000)
        _get_audit().log_error("mark_superseded", None, input_params, exc, duration)
        raise


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _query_memories_by_type(
    project_id: str,
    memory_type: MemoryType,
    component: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Fetch memories of a specific type from PostgreSQL.

    Args:
        project_id:  UUID string.
        memory_type: MemoryType enum value to filter by.
        component:   Optional component name for additional filtering.

    Returns:
        List of dicts with standardised keys.
    """
    pg = _get_pg()
    # Normalise project_id
    norm_project_id = pg._normalize_uuid(project_id)

    query = """
        SELECT
            id,
            title,
            content,
            rationale,
            components,
            file_paths,
            tags,
            created_at,
            is_superseded,
            superseded_by,
            confidence_score
        FROM memories
        WHERE project_id = %s
          AND memory_type = %s
          AND is_superseded = FALSE
    """
    params: list = [norm_project_id, memory_type.value]

    if component:
        query += " AND %s = ANY(components)"
        params.append(component)

    query += " ORDER BY created_at DESC"

    rows = []
    try:
        with pg.get_connection() as conn:
            from psycopg2.extras import RealDictCursor
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                for row in cur.fetchall():
                    rows.append({
                        "id": str(row["id"]),
                        "title": row["title"] or "",
                        "content": row["content"] or "",
                        "rationale": row["rationale"] or "",
                        "components": list(row["components"] or []),
                        "files": list(row["file_paths"] or []),
                        "tags": list(row["tags"] or []),
                        "date": row["created_at"].isoformat() if row["created_at"] else None,
                        "is_superseded": bool(row["is_superseded"]),
                        "superseded_by": str(row["superseded_by"]) if row["superseded_by"] else None,
                        "confidence_score": float(row["confidence_score"] or 1.0),
                    })
    except Exception as exc:
        print(f"[tools] PostgreSQL query failed for {memory_type.value}: {exc}")

    return rows
