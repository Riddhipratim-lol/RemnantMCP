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
from remnant.config import settings
import re
import subprocess
import uuid
from functools import lru_cache
from typing import Optional

from remnant.storage.postgres import PostgresStorage


# ---------------------------------------------------------------------------
# Server-side root resolver
# ---------------------------------------------------------------------------

def resolve_server_root(client_project_root: Optional[str] = None) -> str:
    """
    Resolve the effective workspace root **from the server's perspective**.

    Problem this solves
    -------------------
    When RemnantMCP is deployed remotely (Render, Railway, etc.) an AI client
    may pass ``project_root`` as an absolute local path such as
    ``/Users/alice/Projects/MyApp``.  That path does not exist on the server
    filesystem, so:

    * ``git remote get-url origin`` fails silently (NoSuchPathError),
    * the project UUID is derived from the inaccessible local path instead of
      the git remote URL, producing a *different* UUID than the one used when
      memories were originally stored → queries return 0 results.

    Resolution strategy
    -------------------
    1. If ``client_project_root`` is provided **and exists** on the current
       filesystem, use it as-is (correct for local stdio deployments).
    2. Otherwise fall back to ``REMNANT_PROJECT_ROOT`` env var (set by the
       server's deployment config / IDE MCP env block).
    3. Final fallback: ``os.getcwd()`` (the server process working directory).

    Args:
        client_project_root: The ``project_root`` value supplied by the AI
                             tool call (may be None or a non-existent path).

    Returns:
        An absolute path string that is guaranteed to exist on the current
        filesystem, or the best available server-side root if nothing is
        accessible.
    """
    # Fast path: caller supplied nothing — use server defaults immediately.
    if not client_project_root:
        return settings.remnant_project_root or os.getcwd()

    # Check if the supplied path is accessible on *this* filesystem.
    if os.path.isdir(client_project_root):
        return client_project_root

    # The path doesn't exist here — this server is running remotely and
    # received a local machine path from the AI client.  Silently fall back
    # to the server-configured root so project_id resolution stays stable.
    server_root = settings.remnant_project_root or os.getcwd()
    print(
        f"[ProjectDetector] project_root '{client_project_root}' is not accessible "
        f"on this server filesystem. Falling back to server root: '{server_root}'. "
        "This is expected when RemnantMCP is deployed remotely and the AI client "
        "passes a local machine path."
    )
    return server_root


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


# Generic directory names that are meaningless as project identifiers.
# When a server (e.g. Render) runs from /opt/render/project/src the basename
# would be "src" — useless as a project name.
_GENERIC_NAMES = frozenset({"src", ".", "..", "project", "app", "code", "workspace", "unknown"})


def _meaningful_basename(abs_path: str) -> str:
    """
    Return the most meaningful directory name from an absolute path.

    Walks up the directory tree until it finds a basename that is not in the
    set of known generic names. Falls back to the raw basename if all
    ancestors are also generic.

    Examples::
        /opt/render/project/src  →  "project"   (skips "src")
        /Users/alice/Projects/MyApp  →  "MyApp"
        /  →  "unknown"
    """
    parts = os.path.normpath(abs_path).split(os.sep)
    # Traverse from the leaf toward the root, skipping generic segments
    for part in reversed(parts):
        if part and part.lower() not in _GENERIC_NAMES:
            return part
    # All segments are generic — fall back
    return os.path.basename(abs_path) or "unknown"



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
        root = resolve_server_root(project_root)

        raw_url = get_git_remote_url(root)
        if raw_url:
            canonical = _normalize_remote_url(raw_url)
            project_id = _url_to_deterministic_uuid(canonical)
            # Use the last meaningful path segment of the remote URL as the name
            project_name = canonical.split("/")[-1] or canonical
        else:
            # Fallback: derive project identity from the directory path itself
            canonical = os.path.abspath(root)
            project_id = str(uuid.uuid5(uuid.NAMESPACE_OID, canonical))
            project_name = _meaningful_basename(canonical)

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

