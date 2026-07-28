"""
Layer 5 — MCP Audit Logger

Captures every MCP tool invocation (request + response metadata) to the
`mcp_audit_log` table in PostgreSQL for audit, debugging, and usage analysis.

The logger is designed to be non-fatal: if the database is unavailable, the
MCP tool call proceeds normally and the log failure is printed to stderr.
"""

import json
import os
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from remnant.storage.postgres import PostgresStorage


# ---------------------------------------------------------------------------
# Schema helper (called once on server init to create the audit table)
# ---------------------------------------------------------------------------

_AUDIT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS mcp_audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name       TEXT        NOT NULL,
    project_id      UUID,
    input_params    JSONB,
    output_summary  TEXT,
    status          TEXT        NOT NULL DEFAULT 'success',
    error_message   TEXT,
    duration_ms     INTEGER,
    invoked_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def ensure_audit_table(db_storage: Optional[PostgresStorage] = None) -> None:
    """
    Idempotently create the `mcp_audit_log` table.
    Safe to call multiple times (uses CREATE TABLE IF NOT EXISTS).
    """
    db = db_storage or PostgresStorage()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_AUDIT_TABLE_DDL)
            conn.commit()
    except Exception as exc:
        print(f"[AuditLogger] Could not create audit table: {exc}")


# ---------------------------------------------------------------------------
# Audit logger class
# ---------------------------------------------------------------------------

class AuditLogger:
    """
    Thin wrapper that logs MCP tool invocations to PostgreSQL.

    Usage::

        logger = AuditLogger()
        log_id = logger.start("remember_session", project_id, {"query": "..."})
        ...
        logger.finish(log_id, output_summary="3 memories extracted", duration_ms=420)

    Or in a single call when both input and output are available::

        logger.log("list_decisions", project_id, params, summary, duration_ms=120)
    """

    def __init__(self, db_storage: Optional[PostgresStorage] = None):
        self._db = db_storage or PostgresStorage()

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def log(
        self,
        tool_name: str,
        project_id: Optional[str],
        input_params: Optional[Dict[str, Any]],
        output_summary: Optional[str] = None,
        status: str = "success",
        error_message: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> Optional[str]:
        """
        Write a completed audit record in a single call.

        Returns the generated log UUID (str) on success, or None if the
        database write failed.
        """
        log_id = str(uuid.uuid4())
        try:
            with self._db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO mcp_audit_log
                            (id, tool_name, project_id, input_params,
                             output_summary, status, error_message, duration_ms, invoked_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            log_id,
                            tool_name,
                            project_id,
                            json.dumps(self._sanitise(input_params)) if input_params else None,
                            output_summary,
                            status,
                            error_message,
                            duration_ms,
                            datetime.now(timezone.utc),
                        ),
                    )
                conn.commit()
            return log_id
        except Exception as exc:
            print(f"[AuditLogger] Failed to write audit log for '{tool_name}': {exc}")
            return None

    def log_error(
        self,
        tool_name: str,
        project_id: Optional[str],
        input_params: Optional[Dict[str, Any]],
        exc: Exception,
        duration_ms: Optional[int] = None,
    ) -> Optional[str]:
        """Convenience method to log a tool invocation that raised an exception."""
        return self.log(
            tool_name=tool_name,
            project_id=project_id,
            input_params=input_params,
            output_summary=None,
            status="error",
            error_message=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitise(params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove or truncate fields that should not be stored verbatim
        (e.g. long chat transcripts that would bloat the audit table).
        """
        TRUNCATE_KEYS = {"chat_transcript", "session_notes", "context_block"}
        MAX_LEN = 500

        sanitised: Dict[str, Any] = {}
        for key, value in params.items():
            if key in TRUNCATE_KEYS and isinstance(value, str) and len(value) > MAX_LEN:
                sanitised[key] = value[:MAX_LEN] + "…[truncated]"
            else:
                sanitised[key] = value
        return sanitised
