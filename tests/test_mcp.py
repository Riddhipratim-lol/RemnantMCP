"""
Tests for Layer 5 — MCP Server (Tool Interface)

Covers:
    1. Project Detection (project.py)
       - URL normalisation (SSH, HTTPS, edge cases)
       - Deterministic UUID generation
       - ProjectDetector.resolve() with mocked PostgresStorage

    2. Audit Logger (audit.py)
       - ensure_audit_table() table creation
       - AuditLogger.log() happy path
       - AuditLogger.log_error() error path
       - _sanitise() truncation behaviour

    3. Tool Functions (tools.py)
       - remember_session: no artifacts path
       - remember_session: full pipeline path (mocked Layer 1 + 2)
       - recall_context: delegates to RetrievalCoordinator
       - list_decisions: queries PostgreSQL correctly
       - get_failed_approaches: queries PostgreSQL correctly
       - mark_superseded: calls pg.mark_superseded correctly

    4. MCP Server Registrations (mcp_server.py)
       - Server is instantiated and has the five expected tools
       - Tool names are correctly registered

All external I/O (DB, git, LangGraph, retrieval) is mocked.
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, Mock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Project Detection — mcp/project.py
# ---------------------------------------------------------------------------


class TestUrlNormalisation:
    """Tests for _normalize_remote_url helper."""

    def test_ssh_format(self):
        from remnant.mcp.project import _normalize_remote_url

        result = _normalize_remote_url("git@github.com:org/repo.git")
        assert result == "github.com/org/repo"

    def test_https_format_with_git_suffix(self):
        from remnant.mcp.project import _normalize_remote_url

        result = _normalize_remote_url("https://github.com/org/repo.git")
        assert result == "github.com/org/repo"

    def test_https_format_without_git_suffix(self):
        from remnant.mcp.project import _normalize_remote_url

        result = _normalize_remote_url("https://github.com/org/repo")
        assert result == "github.com/org/repo"

    def test_lowercase_output(self):
        from remnant.mcp.project import _normalize_remote_url

        result = _normalize_remote_url("https://GitHub.COM/Org/Repo.git")
        assert result == result.lower()

    def test_fallback_strips_git_suffix(self):
        from remnant.mcp.project import _normalize_remote_url

        result = _normalize_remote_url("file:///local/path/repo.git")
        assert not result.endswith(".git")

    def test_ssh_with_subdomain(self):
        from remnant.mcp.project import _normalize_remote_url

        result = _normalize_remote_url("git@gitlab.company.com:team/project.git")
        assert result == "gitlab.company.com/team/project"


class TestDeterministicUUID:
    """Tests for _url_to_deterministic_uuid."""

    def test_same_url_produces_same_uuid(self):
        from remnant.mcp.project import _url_to_deterministic_uuid

        uuid1 = _url_to_deterministic_uuid("github.com/org/repo")
        uuid2 = _url_to_deterministic_uuid("github.com/org/repo")
        assert uuid1 == uuid2

    def test_different_urls_produce_different_uuids(self):
        from remnant.mcp.project import _url_to_deterministic_uuid

        uuid1 = _url_to_deterministic_uuid("github.com/org/repo-a")
        uuid2 = _url_to_deterministic_uuid("github.com/org/repo-b")
        assert uuid1 != uuid2

    def test_output_is_valid_uuid(self):
        from remnant.mcp.project import _url_to_deterministic_uuid

        result = _url_to_deterministic_uuid("github.com/org/repo")
        # Should not raise
        uuid.UUID(result)


class TestGetGitRemoteUrl:
    """Tests for get_git_remote_url helper."""

    def test_returns_url_on_success(self, tmp_path):
        from remnant.mcp.project import get_git_remote_url

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/org/repo.git\n"

        with patch("subprocess.run", return_value=mock_result):
            result = get_git_remote_url(str(tmp_path))
        assert result == "https://github.com/org/repo.git"

    def test_returns_none_on_nonzero_exit(self, tmp_path):
        from remnant.mcp.project import get_git_remote_url

        mock_result = Mock()
        mock_result.returncode = 128  # Not a git repo
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = get_git_remote_url(str(tmp_path))
        assert result is None

    def test_returns_none_on_file_not_found(self, tmp_path):
        from remnant.mcp.project import get_git_remote_url

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = get_git_remote_url(str(tmp_path))
        assert result is None

    def test_returns_none_on_timeout(self, tmp_path):
        import subprocess

        from remnant.mcp.project import get_git_remote_url

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            result = get_git_remote_url(str(tmp_path))
        assert result is None


class TestProjectDetector:
    """Tests for ProjectDetector.resolve()."""

    def _make_detector(self, mock_pg):
        from remnant.mcp.project import ProjectDetector

        return ProjectDetector(db_storage=mock_pg)

    def test_resolves_from_git_remote(self, tmp_path):
        mock_pg = MagicMock()
        mock_pg._normalize_uuid.side_effect = lambda x: x
        mock_pg.get_or_create_project.return_value = "abc-123"

        detector = self._make_detector(mock_pg)

        with patch(
            "remnant.mcp.project.get_git_remote_url",
            return_value="https://github.com/org/repo.git",
        ):
            project_id = detector.resolve(str(tmp_path))

        assert project_id == "abc-123"
        mock_pg.get_or_create_project.assert_called_once()

    def test_falls_back_to_path_when_no_git(self, tmp_path):
        mock_pg = MagicMock()
        mock_pg.get_or_create_project.return_value = "fallback-uuid"

        detector = self._make_detector(mock_pg)

        with patch("remnant.mcp.project.get_git_remote_url", return_value=None):
            project_id = detector.resolve(str(tmp_path))

        assert project_id == "fallback-uuid"

    def test_returns_computed_uuid_when_db_fails(self, tmp_path):
        mock_pg = MagicMock()
        mock_pg.get_or_create_project.side_effect = Exception("DB offline")

        detector = self._make_detector(mock_pg)

        with patch(
            "remnant.mcp.project.get_git_remote_url",
            return_value="https://github.com/org/repo.git",
        ):
            project_id = detector.resolve(str(tmp_path))

        # Should still return a valid UUID (computed from URL)
        uuid.UUID(project_id)


# ---------------------------------------------------------------------------
# 2. Audit Logger — mcp/audit.py
# ---------------------------------------------------------------------------


class TestEnsureAuditTable:
    """Tests for ensure_audit_table()."""

    def test_creates_table_successfully(self):
        from remnant.mcp.audit import ensure_audit_table

        mock_pg = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_pg.get_connection.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_pg.get_connection.return_value.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

        # Should not raise
        ensure_audit_table(db_storage=mock_pg)
        mock_cursor.execute.assert_called_once()

    def test_handles_db_exception_gracefully(self):
        from remnant.mcp.audit import ensure_audit_table

        mock_pg = MagicMock()
        mock_pg.get_connection.side_effect = Exception("DB offline")

        # Should not raise
        ensure_audit_table(db_storage=mock_pg)


class TestAuditLoggerSanitise:
    """Tests for AuditLogger._sanitise() static method."""

    def test_truncates_long_chat_transcript(self):
        from remnant.mcp.audit import AuditLogger

        params = {"chat_transcript": "x" * 1000, "project_id": "abc"}
        result = AuditLogger._sanitise(params)
        assert len(result["chat_transcript"]) < 1000
        assert result["chat_transcript"].endswith("…[truncated]")

    def test_leaves_short_values_unchanged(self):
        from remnant.mcp.audit import AuditLogger

        params = {"chat_transcript": "short", "project_id": "abc"}
        result = AuditLogger._sanitise(params)
        assert result["chat_transcript"] == "short"

    def test_leaves_non_sensitive_keys_unchanged(self):
        from remnant.mcp.audit import AuditLogger

        params = {"query": "x" * 1000, "project_id": "abc"}
        result = AuditLogger._sanitise(params)
        assert len(result["query"]) == 1000


class TestAuditLoggerLog:
    """Tests for AuditLogger.log() and log_error()."""

    def _make_logger(self):
        from remnant.mcp.audit import AuditLogger

        mock_pg = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_pg.get_connection.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_pg.get_connection.return_value.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

        return AuditLogger(db_storage=mock_pg), mock_cursor

    def test_log_returns_uuid_on_success(self):
        logger, _ = self._make_logger()
        result = logger.log("test_tool", "proj-123", {"key": "val"}, "summary")
        assert result is not None
        uuid.UUID(result)

    def test_log_executes_insert(self):
        logger, mock_cursor = self._make_logger()
        logger.log("test_tool", "proj-123", {"key": "val"}, "summary")
        mock_cursor.execute.assert_called_once()

    def test_log_returns_none_on_db_error(self):
        from remnant.mcp.audit import AuditLogger

        mock_pg = MagicMock()
        mock_pg.get_connection.side_effect = Exception("DB offline")
        logger = AuditLogger(db_storage=mock_pg)

        result = logger.log("test_tool", "proj-123", {}, "summary")
        assert result is None

    def test_log_error_sets_status_to_error(self):
        logger, mock_cursor = self._make_logger()
        logger.log_error("test_tool", "proj-123", {}, ValueError("boom"))
        # The execute should have been called with status='error'
        call_args = mock_cursor.execute.call_args
        assert call_args is not None
        params = call_args[0][1]
        # status is at index 5 in the INSERT parameters tuple
        assert params[5] == "error"


# ---------------------------------------------------------------------------
# 3. Tool Functions — mcp/tools.py
# ---------------------------------------------------------------------------


class TestRememberSession:
    """Tests for remember_session() tool function."""

    def test_returns_zero_memories_when_no_artifacts(self):
        from remnant.mcp.tools import remember_session

        mock_pg = MagicMock()
        mock_pg._normalize_uuid.side_effect = lambda x: x
        mock_pg.get_or_create_project.return_value = "proj-uuid"

        mock_coordinator = MagicMock()
        mock_coordinator.ingest_session.return_value = ("session-uuid", [])  # No artifacts

        mock_detector = MagicMock()
        mock_detector.resolve.return_value = "proj-uuid"

        mock_audit = MagicMock()
        mock_audit.log.return_value = "log-uuid"

        with (
            patch("remnant.mcp.tools._get_pg", return_value=mock_pg),
            patch("remnant.mcp.tools._get_audit", return_value=mock_audit),
            patch("remnant.mcp.tools._get_project_detector", return_value=mock_detector),
            patch("remnant.mcp.tools.IngestionCoordinator", return_value=mock_coordinator),
        ):
            result = remember_session(
                project_id="proj-uuid",
                project_root="/tmp/test",
            )

        assert result["memories_extracted"] == 0
        assert "session_id" in result
        assert "No new artifacts" in result["summary"]

    def test_returns_memory_count_from_storage_results(self):
        from remnant.structures import ArtifactObject, SourceType
        from remnant.mcp.tools import remember_session

        mock_artifact = MagicMock(spec=ArtifactObject)
        session_id = str(uuid.uuid4())

        mock_pg = MagicMock()
        mock_coordinator = MagicMock()
        mock_coordinator.ingest_session.return_value = (session_id, [mock_artifact])

        mock_detector = MagicMock()
        mock_detector.resolve.return_value = "proj-uuid"

        mock_audit = MagicMock()
        mock_audit.log.return_value = "log-uuid"

        mock_graph = MagicMock()
        mock_graph.invoke.return_value = {
            "storage_results": {
                "status": "success",
                "memories_written": 3,
                "postgres": True,
                "qdrant": True,
                "neo4j": False,
                "errors": [],
            },
            "final_memories": [],
        }

        with (
            patch("remnant.mcp.tools._get_pg", return_value=mock_pg),
            patch("remnant.mcp.tools._get_audit", return_value=mock_audit),
            patch("remnant.mcp.tools._get_project_detector", return_value=mock_detector),
            patch("remnant.mcp.tools.IngestionCoordinator", return_value=mock_coordinator),
            patch("remnant.mcp.tools.build_graph", return_value=mock_graph),
        ):
            result = remember_session(
                project_id="proj-uuid",
                project_root="/tmp/test",
            )

        assert result["memories_extracted"] == 3
        assert result["session_id"] == session_id
        assert result["storage"]["postgres"] is True
        assert result["storage"]["neo4j"] is False


class TestRecallContext:
    """Tests for recall_context() tool function."""

    def test_returns_context_block_and_metadata(self):
        from remnant.mcp.tools import recall_context
        from remnant.retrieval.coordinator import RetrievalResult

        mock_result = RetrievalResult(
            context_block="=== PROJECT MEMORY CONTEXT ===\n\n=== END MEMORY CONTEXT ===",
            memory_ids=["mem-1", "mem-2"],
            retrieved_count=5,
            fallback_used=False,
        )

        mock_coord = MagicMock()
        mock_coord.retrieve.return_value = mock_result

        mock_detector = MagicMock()
        mock_detector.resolve.return_value = "proj-uuid"

        mock_audit = MagicMock()

        with (
            patch("remnant.mcp.tools._get_retrieval_coordinator", return_value=mock_coord),
            patch("remnant.mcp.tools._get_project_detector", return_value=mock_detector),
            patch("remnant.mcp.tools._get_audit", return_value=mock_audit),
            patch("remnant.mcp.tools._get_pg", return_value=MagicMock()),
        ):
            result = recall_context(
                query="implement authentication",
                project_id="proj-uuid",
            )

        assert "context_block" in result
        assert result["retrieved_count"] == 5
        assert result["fallback_used"] is False
        assert "mem-1" in result["memory_ids"]

    def test_passes_filters_to_coordinator(self):
        from remnant.mcp.tools import recall_context
        from remnant.retrieval.coordinator import RetrievalResult

        mock_result = RetrievalResult(
            context_block="",
            memory_ids=[],
            retrieved_count=0,
            fallback_used=True,
        )

        mock_coord = MagicMock()
        mock_coord.retrieve.return_value = mock_result

        mock_detector = MagicMock()
        mock_detector.resolve.return_value = "proj-uuid"

        with (
            patch("remnant.mcp.tools._get_retrieval_coordinator", return_value=mock_coord),
            patch("remnant.mcp.tools._get_project_detector", return_value=mock_detector),
            patch("remnant.mcp.tools._get_audit", return_value=MagicMock()),
            patch("remnant.mcp.tools._get_pg", return_value=MagicMock()),
        ):
            recall_context(
                query="auth bug",
                project_id="proj-uuid",
                component="auth-service",
                memory_types=["FAILED_APPROACH"],
                max_tokens=1000,
            )

        call_kwargs = mock_coord.retrieve.call_args[1]
        assert call_kwargs["component"] == "auth-service"
        assert call_kwargs["memory_types"] == ["FAILED_APPROACH"]
        assert call_kwargs["max_tokens"] == 1000


class TestListDecisions:
    """Tests for list_decisions() tool function."""

    def test_returns_empty_list_when_no_decisions(self):
        from remnant.mcp.tools import list_decisions

        mock_pg = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_pg._normalize_uuid.side_effect = lambda x: x
        mock_pg.get_connection.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_pg.get_connection.return_value.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

        mock_detector = MagicMock()
        mock_detector.resolve.return_value = "proj-uuid"

        with (
            patch("remnant.mcp.tools._get_pg", return_value=mock_pg),
            patch("remnant.mcp.tools._get_project_detector", return_value=mock_detector),
            patch("remnant.mcp.tools._get_audit", return_value=MagicMock()),
        ):
            result = list_decisions(project_id="proj-uuid")

        assert result["total"] == 0
        assert result["decisions"] == []

    def test_returns_decisions_from_db(self):
        from remnant.mcp.tools import list_decisions

        memory_id = str(uuid.uuid4())
        fake_row = {
            "id": uuid.UUID(memory_id),
            "title": "Use JWT",
            "content": "JWT for auth",
            "rationale": "Stateless",
            "components": ["auth"],
            "file_paths": ["src/auth.py"],
            "tags": ["auth"],
            "created_at": datetime.now(timezone.utc),
            "is_superseded": False,
            "superseded_by": None,
            "confidence_score": 0.95,
        }

        mock_pg = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [fake_row]

        mock_pg._normalize_uuid.side_effect = lambda x: x
        mock_pg.get_connection.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_pg.get_connection.return_value.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

        mock_detector = MagicMock()
        mock_detector.resolve.return_value = "proj-uuid"

        with (
            patch("remnant.mcp.tools._get_pg", return_value=mock_pg),
            patch("remnant.mcp.tools._get_project_detector", return_value=mock_detector),
            patch("remnant.mcp.tools._get_audit", return_value=MagicMock()),
        ):
            result = list_decisions(project_id="proj-uuid")

        assert result["total"] == 1
        assert result["decisions"][0]["title"] == "Use JWT"


class TestGetFailedApproaches:
    """Tests for get_failed_approaches() tool function."""

    def test_returns_formatted_failed_approaches(self):
        from remnant.mcp.tools import get_failed_approaches

        memory_id = str(uuid.uuid4())
        fake_row = {
            "id": uuid.UUID(memory_id),
            "title": "WebSocket attempt",
            "content": "Tried WebSockets for real-time updates",
            "rationale": "LB timeout constraints forced SSE instead",
            "components": ["api"],
            "file_paths": [],
            "tags": [],
            "created_at": datetime.now(timezone.utc),
            "is_superseded": False,
            "superseded_by": None,
            "confidence_score": 0.8,
        }

        mock_pg = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [fake_row]

        mock_pg._normalize_uuid.side_effect = lambda x: x
        mock_pg.get_connection.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_pg.get_connection.return_value.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = Mock(return_value=False)

        mock_detector = MagicMock()
        mock_detector.resolve.return_value = "proj-uuid"

        with (
            patch("remnant.mcp.tools._get_pg", return_value=mock_pg),
            patch("remnant.mcp.tools._get_project_detector", return_value=mock_detector),
            patch("remnant.mcp.tools._get_audit", return_value=MagicMock()),
        ):
            result = get_failed_approaches(query="real-time", project_id="proj-uuid")

        assert result["total"] == 1
        fa = result["failed_approaches"][0]
        assert fa["title"] == "WebSocket attempt"
        assert "what_was_tried" in fa
        assert "why_abandoned" in fa


class TestMarkSuperseded:
    """Tests for mark_superseded() tool function."""

    def test_returns_success_true_when_db_succeeds(self):
        from remnant.mcp.tools import mark_superseded

        mock_pg = MagicMock()
        mock_pg.mark_superseded.return_value = True

        with (
            patch("remnant.mcp.tools._get_pg", return_value=mock_pg),
            patch("remnant.mcp.tools._get_audit", return_value=MagicMock()),
        ):
            result = mark_superseded(
                memory_id="mem-123",
                reason="Replaced by new architecture",
                new_memory_id="mem-456",
            )

        assert result["success"] is True
        assert result["memory_id"] == "mem-123"
        assert result["new_memory_id"] == "mem-456"

    def test_returns_success_false_when_memory_not_found(self):
        from remnant.mcp.tools import mark_superseded

        mock_pg = MagicMock()
        mock_pg.mark_superseded.return_value = False

        with (
            patch("remnant.mcp.tools._get_pg", return_value=mock_pg),
            patch("remnant.mcp.tools._get_audit", return_value=MagicMock()),
        ):
            result = mark_superseded(
                memory_id="nonexistent-id",
                reason="Test",
            )

        assert result["success"] is False
        assert "No memory found" in result["message"]

    def test_calls_pg_mark_superseded_with_correct_args(self):
        from remnant.mcp.tools import mark_superseded

        mock_pg = MagicMock()
        mock_pg.mark_superseded.return_value = True

        with (
            patch("remnant.mcp.tools._get_pg", return_value=mock_pg),
            patch("remnant.mcp.tools._get_audit", return_value=MagicMock()),
        ):
            mark_superseded(
                memory_id="mem-123",
                reason="Outdated",
                new_memory_id="mem-456",
            )

        mock_pg.mark_superseded.assert_called_once_with(
            memory_id="mem-123",
            superseded_by="mem-456",
        )


# ---------------------------------------------------------------------------
# 4. MCP Server Tool Registrations — mcp_server.py
# ---------------------------------------------------------------------------


class TestMCPServerRegistrations:
    """Tests that the FastMCP server registers the expected tools."""

    def test_server_has_five_tools(self):
        """
        Import the mcp instance and verify the five tools are registered.
        We patch ensure_audit_table to avoid requiring a live DB on import.
        FastMCP 3.x exposes list_tools() as an async coroutine.
        """
        import asyncio
        with patch("remnant.mcp.audit.ensure_audit_table"):
            from remnant.mcp_server import mcp

        tool_names = _get_registered_tool_names(mcp)
        assert len(tool_names) >= 5, f"Expected ≥5 tools, got: {tool_names}"

    def test_expected_tool_names_are_registered(self):
        """Verify each of the five canonical tool names is present."""
        import asyncio
        with patch("remnant.mcp.audit.ensure_audit_table"):
            from remnant.mcp_server import mcp

        tool_names = _get_registered_tool_names(mcp)
        expected = {
            "remember_session_tool",
            "recall_context_tool",
            "list_decisions_tool",
            "get_failed_approaches_tool",
            "mark_superseded_tool",
        }
        for name in expected:
            assert name in tool_names, f"Tool '{name}' not registered. Found: {tool_names}"


def _get_registered_tool_names(mcp_instance) -> set:
    """
    Helper to extract tool names from a FastMCP 3.x instance.

    FastMCP 3.x exposes list_tools() as an async coroutine that returns
    Tool objects with a .name attribute. We use asyncio.run() to call it
    synchronously from within a pytest test.
    """
    import asyncio

    # Primary path: FastMCP 3.x async list_tools()
    if hasattr(mcp_instance, "list_tools"):
        try:
            result = asyncio.run(mcp_instance.list_tools())
            if result and hasattr(result[0], "name"):
                return {t.name for t in result}
        except Exception:
            pass

    # Fallback: inspect internal tool manager
    if hasattr(mcp_instance, "_tool_manager"):
        tm = mcp_instance._tool_manager
        for attr in ("tools", "_tools"):
            val = getattr(tm, attr, None)
            if isinstance(val, dict):
                return set(val.keys())

    return set()
