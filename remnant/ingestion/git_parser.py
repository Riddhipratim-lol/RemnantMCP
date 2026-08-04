from typing import Dict, List, Tuple, Optional
import git

def parse_git_repo(repo_path: str, last_processed_sha: Optional[str] = None) -> Tuple[str, List[str], List[Dict]]:
    """
    Parse git changes from the repository.

    Collection order (most-recent-first):
      Phase 1 — Unstaged + staged workspace diffs (current WIP, highest priority).
      Phase 2 — Committed history: incremental range if last_processed_sha is
                 available, otherwise HEAD~1..HEAD (or initial commit show).

    Returns:
        git_diff_raw (str): The raw combined git diff of changes.
        commit_messages (List[str]): List of commit messages in the parsed range.
        file_change_stats (List[Dict]): List of dicts, each with 'file_path',
            'insertions', 'deletions'.

    Raises:
        ValueError: If repo_path does not exist on the current filesystem.
                    This is the expected failure mode when a cloud-deployed server
                    receives a local machine path (e.g. /Users/alice/Projects/MyApp
                    while running on Render at /opt/render/project/src).
    """
    try:
        repo = git.Repo(repo_path)
    except git.exc.NoSuchPathError:
        raise ValueError(
            f"Repository path does not exist on this server: '{repo_path}'. "
            "If RemnantMCP is running as a cloud service (Render, Railway, etc.), "
            "the project_root must be a path accessible on the server filesystem, "
            "not a local machine path. Leave project_root empty to use the server's "
            "working directory, or pass the correct server-side path."
        )

    # Check if repo is empty or HEAD is invalid
    if repo.bare or not repo.head.is_valid():
        return "", [], []

    commit_messages: List[str] = []
    diff_parts: List[str] = []
    file_stats_map: Dict[str, Dict[str, int]] = {}  # file_path -> {insertions, deletions}

    # ------------------------------------------------------------------
    # Helper: accumulate --numstat output into file_stats_map
    # ------------------------------------------------------------------
    def parse_numstat(numstat_output: str) -> None:
        for line in numstat_output.splitlines():
            if line.strip():
                parts = line.split('\t')
                if len(parts) >= 3:
                    try:
                        ins = int(parts[0]) if parts[0] != '-' else 0
                        dels = int(parts[1]) if parts[1] != '-' else 0
                        filepath = parts[2]
                        if filepath in file_stats_map:
                            file_stats_map[filepath]["insertions"] += ins
                            file_stats_map[filepath]["deletions"] += dels
                        else:
                            file_stats_map[filepath] = {"insertions": ins, "deletions": dels}
                    except ValueError:
                        pass  # skip binary files / malformed lines

    # ------------------------------------------------------------------
    # Phase 1 — Workspace diffs (HIGHEST PRIORITY: most recent WIP)
    # Staged and unstaged changes capture the current session state.
    # Collected FIRST so a Phase 2 failure never silences them.
    # ------------------------------------------------------------------
    try:
        unstaged_diff = repo.git.diff()
        if unstaged_diff:
            diff_parts.append(unstaged_diff)
            parse_numstat(repo.git.diff('--numstat'))
    except Exception:
        pass

    try:
        staged_diff = repo.git.diff('--cached')
        if staged_diff:
            diff_parts.append(staged_diff)
            parse_numstat(repo.git.diff('--numstat', '--cached'))
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Phase 2 — Committed history (incremental range or HEAD~1..HEAD)
    # ------------------------------------------------------------------
    resolved_sha = last_processed_sha  # local alias so we can reset without mutating arg

    if resolved_sha:
        try:
            base_commit = repo.commit(resolved_sha)
            head_commit = repo.head.commit

            if base_commit.hexsha != head_commit.hexsha:
                diff_text = repo.git.diff(resolved_sha, 'HEAD')
                if diff_text:
                    diff_parts.append(diff_text)

                commits = list(repo.iter_commits(f"{resolved_sha}..HEAD"))
                commit_messages = [c.message.strip() for c in commits]

                parse_numstat(repo.git.diff('--numstat', resolved_sha, 'HEAD'))
        except Exception:
            # Fall back to single-commit diff below
            resolved_sha = None

    if not resolved_sha:
        try:
            head_commit = repo.head.commit
            if len(head_commit.parents) > 0:
                diff_text = repo.git.diff('HEAD~1', 'HEAD')
                if diff_text:
                    diff_parts.append(diff_text)
                commit_messages = [head_commit.message.strip()]
                parse_numstat(repo.git.diff('--numstat', 'HEAD~1', 'HEAD'))
            else:
                # Initial commit — no parents
                diff_text = repo.git.show(head_commit.hexsha)
                if diff_text:
                    diff_parts.append(diff_text)
                commit_messages = [head_commit.message.strip()]
                parse_numstat(repo.git.diff_tree('--numstat', head_commit.hexsha, root=True))
        except Exception:
            pass

    git_diff_raw = "\n".join(diff_parts)
    file_change_stats = [
        {"file_path": fp, "insertions": s["insertions"], "deletions": s["deletions"]}
        for fp, s in file_stats_map.items()
    ]

    return git_diff_raw, commit_messages, file_change_stats
