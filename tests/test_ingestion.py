import pytest
from datetime import datetime, timezone
import uuid
from typing import Dict, List, Optional

from remnant.structures import ArtifactObject, SourceType
from remnant.ingestion.redactor import redact_content
from remnant.ingestion.grouper import calculate_sha256, SessionGrouper
from remnant.ingestion.coordinator import IngestionCoordinator
from remnant.ingestion.git_parser import parse_git_repo


# ==========================================
# 1. Tests for redactor.py
# ==========================================
def test_redact_github_token():
    text = "Here is my github token: ghp_1234567890abcdefghijklmnopqrstuvwxyz"
    expected = "Here is my github token: <REDACTED>"
    assert redact_content(text) == expected


def test_redact_generic_api_key():
    text1 = "api_key = \"mysecretkeyvalue123\""
    text2 = "API-KEY: some-long-secret-key-string"
    text3 = "{\"secret\": \"supersecrettoken123\"}"
    text4 = "password=SuperPassword123"
    
    assert "mysecretkeyvalue123" not in redact_content(text1)
    assert "<REDACTED>" in redact_content(text1)
    assert "<REDACTED>" in redact_content(text2)
    assert "<REDACTED>" in redact_content(text3)
    assert "<REDACTED>" in redact_content(text4)


def test_redact_database_url():
    text = "Connecting to postgresql://username:secretpassword123@db.example.com:5432/mydb..."
    assert "secretpassword123" not in redact_content(text)
    assert "<REDACTED>" in redact_content(text)


# ==========================================
# 2. Tests for grouper.py
# ==========================================
class MockPostgresStorage:
    def __init__(self):
        self.projects = {}
        self.sessions = {}
        self.session_logs = set()
        self.last_shas = {}

    def get_or_create_project(self, project_id: str, name: str, repo_path: str) -> str:
        self.projects[project_id] = {"name": name, "repo_path": repo_path}
        return project_id

    def get_active_session(self, project_id: str, window_hours: int = 4) -> Optional[str]:
        # Return existing session if any
        for sid, sess in self.sessions.items():
            if sess["project_id"] == project_id and sess["status"] == "ACTIVE":
                return sid
        return None

    def create_session(self, session_id: str, project_id: str) -> str:
        self.sessions[session_id] = {
            "project_id": project_id,
            "started_at": datetime.now(timezone.utc),
            "status": "ACTIVE",
            "artifact_count": 0
        }
        return session_id

    def is_hash_processed(self, project_id: str, content_hash: str) -> bool:
        return content_hash in self.session_logs

    def log_artifact(self, project_id: str, session_id: str, artifact_hash: str, processed_sha: Optional[str] = None) -> None:
        self.session_logs.add(artifact_hash)
        if session_id in self.sessions:
            self.sessions[session_id]["artifact_count"] += 1
        if processed_sha:
            self.last_shas[project_id] = processed_sha

    def get_last_processed_sha(self, project_id: str) -> Optional[str]:
        return self.last_shas.get(project_id)


def test_calculate_sha256():
    assert calculate_sha256("hello") == calculate_sha256("hello")
    assert calculate_sha256("hello") != calculate_sha256("world")
    assert len(calculate_sha256("test")) == 64


def test_session_grouper_new_and_existing_sessions():
    mock_db = MockPostgresStorage()
    grouper = SessionGrouper(db_storage=mock_db, window_hours=4)
    
    project_id = "test-project-uuid"
    raw_artifacts = [
        {"source_type": SourceType.CHAT, "raw_content": "hello world chat"},
        {"source_type": SourceType.ERROR_LOG, "raw_content": "database connection error"}
    ]
    
    # 1. First run: Should create a new session
    session_id_1, artifacts_1 = grouper.process_and_group(raw_artifacts, project_id)
    assert session_id_1 is not None
    assert len(artifacts_1) == 2
    assert artifacts_1[0].session_id == session_id_1
    assert artifacts_1[1].session_id == session_id_1
    assert mock_db.sessions[session_id_1]["artifact_count"] == 2
    
    # 2. Second run: Should reuse the active session
    raw_artifacts_new = [
        {"source_type": SourceType.CHAT, "raw_content": "new chat message"}
    ]
    session_id_2, artifacts_2 = grouper.process_and_group(raw_artifacts_new, project_id)
    assert session_id_2 == session_id_1
    assert len(artifacts_2) == 1
    assert artifacts_2[0].session_id == session_id_1
    assert mock_db.sessions[session_id_1]["artifact_count"] == 3


def test_session_grouper_deduplication():
    mock_db = MockPostgresStorage()
    grouper = SessionGrouper(db_storage=mock_db, window_hours=4)
    project_id = "test-project-uuid"
    
    raw_artifacts = [
        {"source_type": SourceType.CHAT, "raw_content": "repeated message"},
        {"source_type": SourceType.CHAT, "raw_content": "repeated message"},
        {"source_type": SourceType.CHAT, "raw_content": "unique message"}
    ]
    
    session_id, artifacts = grouper.process_and_group(raw_artifacts, project_id)
    assert len(artifacts) == 2  # One of the repeated messages was deduplicated
    assert artifacts[0].raw_content == "repeated message"
    assert artifacts[1].raw_content == "unique message"
    
    # Re-running the same unique message should deduplicate it completely
    raw_artifacts_2 = [
        {"source_type": SourceType.CHAT, "raw_content": "unique message"}
    ]
    _, artifacts_2 = grouper.process_and_group(raw_artifacts_2, project_id)
    assert len(artifacts_2) == 0


# ==========================================
# 3. Tests for git_parser.py
# ==========================================
def test_parse_git_repo_empty(mocker):
    # Mock git.Repo
    mock_repo = mocker.patch("git.Repo")
    mock_instance = mock_repo.return_value
    mock_instance.bare = True
    
    diff, commits, stats = parse_git_repo("/mock/repo/path")
    assert diff == ""
    assert commits == []
    assert stats == []


def test_parse_git_repo_with_changes(mocker):
    # Mock git.Repo and its properties
    mock_repo = mocker.patch("git.Repo")
    mock_instance = mock_repo.return_value
    mock_instance.bare = False
    mock_instance.head.is_valid.return_value = True
    
    # Mock git commands via repo.git
    mock_instance.git.diff.return_value = ""
    mock_instance.git.show.return_value = "dummy git diff content"
    
    # Mock commit message
    mock_commit = mocker.MagicMock()
    mock_commit.message = "Initial commit message"
    mock_commit.parents = [] # Initial commit
    mock_instance.head.commit = mock_commit
    
    mock_instance.git.diff_tree.return_value = "10\t5\tfile1.py\n2\t0\tfile2.py"
    
    diff, commits, stats = parse_git_repo("/mock/repo/path")
    
    assert "dummy git diff content" in diff
    assert commits == ["Initial commit message"]
    assert len(stats) == 2
    assert stats[0] == {"file_path": "file1.py", "insertions": 10, "deletions": 5}
    assert stats[1] == {"file_path": "file2.py", "insertions": 2, "deletions": 0}


# ==========================================
# 4. Tests for coordinator.py
# ==========================================
def test_coordinator_orchestration(mocker):
    # Mock DB storage
    mock_db = MockPostgresStorage()
    
    # Mock git parser
    mock_parser = mocker.patch("remnant.ingestion.coordinator.parse_git_repo")
    mock_parser.return_value = (
        "git diff content with ghp_1234567890abcdefghijklmnopqrstuvwxyz token", 
        ["Commit msg with api_key = \"mysecretkeyvalue123\""], 
        [{"file_path": "main.py", "insertions": 5, "deletions": 2}]
    )
    
    coordinator = IngestionCoordinator(db_storage=mock_db)
    
    session_id, artifacts = coordinator.ingest_session(
        repo_path="/mock/repo",
        project_id="test-project",
        chat_transcript="Chat showing postgresql://user:pwd@db.host.com:5432/db",
        logs="Error log content"
    )
    
    assert session_id is not None
    # We should have 5 artifacts: GIT_DIFF, COMMIT, FILE_CHANGE, CHAT, ERROR_LOG
    assert len(artifacts) == 5
    
    # Check that redaction occurred across all sources
    for art in artifacts:
        assert "ghp_" not in art.raw_content
        assert "mysecretkeyvalue123" not in art.raw_content
        assert "pwd" not in art.raw_content
