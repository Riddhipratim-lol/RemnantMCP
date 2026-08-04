"""
Layer 5 — FastMCP Server Entrypoint

This module is the public interface for RemnantMCP. It:

  1. Bootstraps environment (loads .env, configures logging).
  2. Ensures the MCP audit log table exists in PostgreSQL.
  3. Registers the five MCP tools using FastMCP's @mcp.tool decorator.
  4. Exposes `mcp.run()` — when launched as a subprocess by an IDE client
     (Cursor, Claude Desktop, Windsurf) FastMCP auto-selects stdio transport;
     when run as a standalone service it uses SSE transport.

Invocation (stdio — default for local single-user use):
    python -m remnant.mcp_server

Or via the project entrypoint:
    python main.py

MCP configuration (add to your IDE's mcpServers config):
    {
      "mcpServers": {
        "remnant": {
          "command": "python",
          "args": ["-m", "remnant.mcp_server"],
          "env": {
            "REMNANT_DB_URL": "postgresql://...",
            "REMNANT_QDRANT_URL": "...",
            "REMNANT_QDRANT_API_KEY": "...",
            "REMNANT_NEO4J_URL": "...",
            "REMNANT_NEO4J_USERNAME": "neo4j",
            "REMNANT_NEO4J_PASSWORD": "...",
            "VOYAGE_API_KEY": "...",
            "GEMINI_API_KEY": "...",
            "REMNANT_PROJECT_ROOT": "${workspaceFolder}"
          }
        }
      }
    }
"""

import os

from dotenv import load_dotenv
from fastmcp import FastMCP

# Load environment variables from .env (no-op if not present)
load_dotenv()

from remnant.mcp.audit import ensure_audit_table
from remnant.mcp.tools import (
    get_failed_approaches,
    list_decisions,
    mark_superseded,
    recall_context,
    remember_session,
)

# ---------------------------------------------------------------------------
# Server initialisation
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="RemnantMCP",
    instructions=(
        "RemnantMCP is a persistent cross-tool project memory system that captures and recalls "
        "engineering decisions, rationale, failed approaches, and constraints across sessions and tools.\n\n"
        "IMPORTANT — MEMORY CAPTURE QUALITY:\n"
        "When calling 'remember_session', ALWAYS pass the full session conversation as 'chat_transcript'. "
        "This is the richest signal for memory extraction. If you pass only 'session_notes' (a short "
        "summary), the system will still extract from it as a fallback, but far fewer and lower-quality "
        "memories will be captured. The full transcript is the preferred input.\n\n"
        "TOOL USAGE:\n"
        "- 'remember_session': Call at the END of every coding session. Pass 'chat_transcript' (the full "
        "conversation text) and 'session_notes' (a brief summary). The system auto-reads git diffs and "
        "commit history from the workspace.\n"
        "- 'recall_context': Call at the START of a session or before implementing anything. Retrieves "
        "relevant decisions, rationale, and past failures for your current task.\n"
        "- 'list_decisions': Lists all stored architectural decisions for the project.\n"
        "- 'get_failed_approaches': Check before proposing a solution — avoids repeating previously-tried "
        "and rejected approaches.\n"
        "- 'mark_superseded': Deprecate an outdated memory when a decision has been replaced."
    ),
)

# Ensure the audit log table exists (idempotent — safe to run on every startup)
try:
    ensure_audit_table()
except Exception as _audit_init_err:
    print(f"[RemnantMCP] Warning: could not initialise audit table: {_audit_init_err}")


# ---------------------------------------------------------------------------
# Tool registrations
# ---------------------------------------------------------------------------

@mcp.tool
def remember_session_tool(
    project_id: str = "",
    chat_transcript: str = "",
    commit_sha: str = "",
    session_notes: str = "",
    project_root: str = "",
) -> dict:
    """
    Capture and persist knowledge from a completed coding session.

    Call this at the **end of every coding session** to extract and store
    architectural decisions, implementation rationale, failed approaches,
    bug resolutions, design tradeoffs, component relationships, and constraints.

    ** IMPORTANT — QUALITY OF CAPTURED MEMORIES **
    Pass the full session conversation as ``chat_transcript`` whenever possible.
    This is the richest signal for the extraction pipeline. If ``chat_transcript``
    is omitted, the system falls back to ``session_notes`` as the sole text
    source — useful but produces fewer and lower-confidence memories.
    The ideal call passes BOTH:
      • chat_transcript — the complete conversation text (AI + developer turns)
      • session_notes   — a brief human summary of what was accomplished

    Args:
        project_id:      (Optional) Explicit project UUID. Auto-detected from
                         git remote URL if omitted.
        chat_transcript: The full session chat transcript text (strongly recommended).
                         When provided, this is the primary extraction source.
        commit_sha:      (Optional) Target git commit SHA. Defaults to HEAD.
        session_notes:   (Optional) Brief summary or additional notes. Used as
                         the sole extraction source when chat_transcript is absent.
        project_root:    (Optional) Absolute path to the workspace directory.
                         Defaults to REMNANT_PROJECT_ROOT env var or cwd.

    Returns:
        Dictionary with keys:
          - memories_extracted (int): Number of new memories stored.
          - session_id (str): UUID of the ingestion session.
          - summary (str): Human-readable summary of what was extracted.
          - storage (dict): Write status per backend (postgres/qdrant/neo4j).
    """
    return remember_session(
        project_id=project_id or None,
        chat_transcript=chat_transcript or None,
        commit_sha=commit_sha or None,
        session_notes=session_notes or None,
        project_root=project_root or None,
    )


@mcp.tool
def recall_context_tool(
    query: str,
    project_id: str = "",
    component: str = "",
    file_path: str = "",
    memory_types: str = "",
    max_tokens: int = 2000,
    project_root: str = "",
) -> dict:
    """
    Retrieve relevant project memories for the current task.

    Call this at the **start of a session** or whenever you need historical
    context about decisions, constraints, or past failures relevant to your
    current task.

    Args:
        query:        Current task description, active code snippet, or question.
        project_id:   (Optional) Project UUID. Auto-detected if omitted.
        component:    (Optional) Component name to scope retrieval
                      (e.g. "auth-service", "database").
        file_path:    (Optional) File path to scope retrieval.
        memory_types: (Optional) Comma-separated list of memory types to filter.
                      Valid values: ARCHITECTURAL_DECISION, IMPLEMENTATION_RATIONALE,
                      FAILED_APPROACH, BUG_RESOLUTION, DESIGN_TRADEOFF,
                      COMPONENT_RELATIONSHIP, CONSTRAINT.
        max_tokens:   Token budget for the returned context block (default: 2000).
        project_root: (Optional) Absolute path to the workspace directory.

    Returns:
        Dictionary with keys:
          - context_block (str): Formatted memory context block for injection.
          - memory_ids (List[str]): UUIDs of the memories included.
          - retrieved_count (int): Total candidates evaluated before packing.
          - fallback_used (bool): True when Qdrant/Neo4j were unavailable.
    """
    # Parse comma-separated memory_types string into a list
    mt_list = (
        [t.strip() for t in memory_types.split(",") if t.strip()]
        if memory_types
        else None
    )
    return recall_context(
        query=query,
        project_id=project_id or None,
        component=component or None,
        file_path=file_path or None,
        memory_types=mt_list,
        max_tokens=max_tokens,
        project_root=project_root or None,
    )


@mcp.tool
def list_decisions_tool(
    project_id: str = "",
    component: str = "",
    project_root: str = "",
) -> dict:
    """
    List all architectural decisions stored for a project.

    Returns a structured catalogue of ARCHITECTURAL_DECISION memories,
    optionally filtered by component.

    Args:
        project_id:   (Optional) Project UUID. Auto-detected if omitted.
        component:    (Optional) Component name to filter decisions.
        project_root: (Optional) Absolute path to the workspace directory.

    Returns:
        Dictionary with keys:
          - decisions (List[dict]): Each decision has id, title, rationale,
            date, files, components, tags, is_superseded.
          - total (int): Total number of decisions returned.
    """
    return list_decisions(
        project_id=project_id or None,
        component=component or None,
        project_root=project_root or None,
    )


@mcp.tool
def get_failed_approaches_tool(
    query: str,
    project_id: str = "",
    project_root: str = "",
) -> dict:
    """
    Retrieve previously-tried approaches that were abandoned.

    Use this before proposing a solution to check whether it has already
    been attempted and rejected.

    Args:
        query:        Topic or task description to search for relevant failures.
        project_id:   (Optional) Project UUID. Auto-detected if omitted.
        project_root: (Optional) Absolute path to the workspace directory.

    Returns:
        Dictionary with keys:
          - failed_approaches (List[dict]): Each entry has id, title,
            what_was_tried, why_abandoned, date, files, components.
          - total (int): Total number of failed approaches returned.
    """
    return get_failed_approaches(
        query=query,
        project_id=project_id or None,
        project_root=project_root or None,
    )


@mcp.tool
def mark_superseded_tool(
    memory_id: str,
    reason: str,
    new_memory_id: str = "",
    project_root: str = "",
) -> dict:
    """
    Mark an outdated memory as superseded.

    Use this when a decision or rationale has become obsolete so that future
    retrievals do not surface stale context.

    Args:
        memory_id:     UUID of the memory to deprecate.
        reason:        Explanation of why this memory is no longer valid.
        new_memory_id: (Optional) UUID of the replacement memory.
        project_root:  (Optional) Absolute path to the workspace directory.

    Returns:
        Dictionary with keys:
          - success (bool): Whether the update succeeded.
          - memory_id (str): The memory that was deprecated.
          - new_memory_id (str | None): The replacement memory UUID, if provided.
          - message (str): Human-readable result message.
    """
    return mark_superseded(
        memory_id=memory_id,
        reason=reason,
        new_memory_id=new_memory_id or None,
        project_root=project_root or None,
    )


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # FastMCP auto-selects stdio when launched as a subprocess by an MCP client,
    # or SSE when run as a standalone HTTP service.
    mcp.run()
