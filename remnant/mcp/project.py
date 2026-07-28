"""
Layer 5 — Project Detection Node

Responsible for:
  - Running `git remote get-url origin` in the active workspace directory.
  - Normalising the raw remote URL into a canonical key (handles SSH vs HTTPS variants).
  - Resolving or creating the project record in PostgreSQL and returning the UUID.

Called once per MCP server startup (or lazily on first tool invocation) so that
all subsequent tool calls can use the stable project_id without re-running git.
"""

import os
import re
import subprocess
import uuid
from functools import lru_cache
from typing import Optional

from remnant.storage.postgres import PostgresStorage


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------

def _normalize_remote_url(raw_url: str) -> str:
    """
    Convert various git remote URL formats into a canonical lower-case key.

    Examples handled:
        git@github.com:org/repo.git  →  github.com/org/repo
        https://github.com/org/repo  →  github.com/org/repo
        https://github.com/org/repo.git  →  github.com/org/repo
    """
    url = raw_url.strip()

    # SSH format: git@host:path.git
    ssh_match = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", url)
    if ssh_match:
        host, path = ssh_match.group(1), ssh_match.group(2)
        return f"{host}/{path}".lower()

    # HTTPS format
    https_match = re.match(r"^https?://([^/]+)/(.+?)(?:\.git)?$", url)
    if https_match:
        host, path = https_match.group(1), https_match.group(2)
        return f"{host}/{path}".lower()

    # Fallback: strip .git suffix and lower-case
    return re.sub(r"\.git$", "", url).lower()


def _url_to_deterministic_uuid(canonical_url: str) -> str:
    """Produce a stable UUID-v5 from a canonical project URL."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, canonical_url))


# ---------------------------------------------------------------------------
# Git helper
# ---------------------------------------------------------------------------

def get_git_remote_url(project_root: str) -> Optional[str]:
    """
    Run `git remote get-url origin` and return the output string.

    Returns None if git is unavailable, the directory is not a repo,
    or there is no remote named `origin`.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Project resolver
# ---------------------------------------------------------------------------

class ProjectDetector:
    """
    Resolves the active project from a workspace directory.

    Strategy:
      1. Try `git remote get-url origin` to obtain a canonical URL.
      2. Derive a deterministic UUID from that URL.
      3. Ensure a project row exists in PostgreSQL (get_or_create_project).
      4. If git is unavailable, fall back to a UUID derived from the directory path.
    """

    def __init__(self, db_storage: Optional[PostgresStorage] = None):
        self._db = db_storage or PostgresStorage()

    def resolve(self, project_root: Optional[str] = None) -> str:
        """
        Return the project_id UUID string for the given workspace root.

        Args:
            project_root: Absolute path to the workspace directory.
                          Defaults to the REMNANT_PROJECT_ROOT env var or cwd.

        Returns:
            Stable UUID string (str) identifying this project.
        """
        root = project_root or os.environ.get("REMNANT_PROJECT_ROOT") or os.getcwd()

        raw_url = get_git_remote_url(root)
        if raw_url:
            canonical = _normalize_remote_url(raw_url)
            project_id = _url_to_deterministic_uuid(canonical)
            project_name = canonical.split("/")[-1] or canonical
        else:
            # Fallback: derive project identity from the directory path itself
            canonical = os.path.abspath(root)
            project_id = str(uuid.uuid5(uuid.NAMESPACE_OID, canonical))
            project_name = os.path.basename(canonical) or "unknown"

        # Ensure the project exists in PostgreSQL (idempotent upsert)
        try:
            resolved_id = self._db.get_or_create_project(
                project_id=project_id,
                name=project_name,
                repo_path=root,
            )
            return resolved_id
        except Exception as exc:
            # DB unavailable — return the computed UUID anyway so the
            # server can still operate in degraded mode
            print(f"[ProjectDetector] PostgreSQL upsert failed: {exc}")
            return project_id
