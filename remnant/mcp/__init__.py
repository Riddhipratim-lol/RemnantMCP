"""
RemnantMCP — Layer 5: MCP Server package.

Public exports:
    ProjectDetector  — resolve git remote URL → project_id
    AuditLogger      — write tool invocation logs to PostgreSQL
    remember_session — Layer 1+2 ingestion pipeline trigger
    recall_context   — Layer 4 retrieval pipeline trigger
    list_decisions   — list ARCHITECTURAL_DECISION memories
    get_failed_approaches — list FAILED_APPROACH memories
    mark_superseded  — soft-deprecate a memory record
"""

from remnant.mcp.audit import AuditLogger, ensure_audit_table
from remnant.mcp.project import ProjectDetector, get_git_remote_url
from remnant.mcp.tools import (
    get_failed_approaches,
    list_decisions,
    mark_superseded,
    recall_context,
    remember_session,
)

__all__ = [
    "AuditLogger",
    "ensure_audit_table",
    "ProjectDetector",
    "get_git_remote_url",
    "remember_session",
    "recall_context",
    "list_decisions",
    "get_failed_approaches",
    "mark_superseded",
]
