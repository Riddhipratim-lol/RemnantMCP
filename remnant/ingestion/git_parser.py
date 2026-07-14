import os
from typing import Dict, List, Tuple, Optional
import git

def parse_git_repo(repo_path: str, last_processed_sha: Optional[str] = None) -> Tuple[str, List[str], List[Dict]]:
    """
    Parse git changes from the repository.
    
    Returns:
        git_diff_raw (str): The raw combined git diff of changes.
        commit_messages (List[str]): List of commit messages in the parsed range.
        file_change_stats (List[Dict]): List of dicts, each with 'file_path', 'insertions', 'deletions'.
    """
    repo = git.Repo(repo_path)
    
    # Check if repo is empty or HEAD is invalid
    if repo.bare or not repo.head.is_valid():
        return "", [], []
    
    commit_messages = []
    diff_parts = []
    file_stats_map = {}  # file_path -> {"insertions": int, "deletions": int}
    
    # helper to parse line-by-line --numstat output
    def parse_numstat(numstat_output: str):
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
                        # Skip if count parsing fails (e.g. binary files)
                        pass

    # 1. Commits history & diffs in range
    if last_processed_sha:
        try:
            # Check if last_processed_sha is in the repo history
            base_commit = repo.commit(last_processed_sha)
            head_commit = repo.head.commit
            
            if base_commit.hexsha != head_commit.hexsha:
                # Get the diff between base_commit and head_commit
                # We can use git command directly or gitpython API.
                # git diff last_processed_sha..HEAD
                diff_text = repo.git.diff(last_processed_sha, 'HEAD')
                if diff_text:
                    diff_parts.append(diff_text)
                
                # Get commits in range last_processed_sha..HEAD (excluding last_processed_sha, including HEAD)
                commits = list(repo.iter_commits(f"{last_processed_sha}..HEAD"))
                commit_messages = [c.message.strip() for c in commits]
                
                # Parse numstat stats
                numstat_out = repo.git.diff('--numstat', last_processed_sha, 'HEAD')
                parse_numstat(numstat_out)
        except Exception:
            # Fall back to default behavior if last_processed_sha is invalid/not found
            last_processed_sha = None
            
    # Default behavior if no last_processed_sha or if parsing with it failed/returned nothing
    if not last_processed_sha:
        try:
            head_commit = repo.head.commit
            if len(head_commit.parents) > 0:
                parent = head_commit.parents[0]
                diff_text = repo.git.diff('HEAD~1', 'HEAD')
                if diff_text:
                    diff_parts.append(diff_text)
                commit_messages = [head_commit.message.strip()]
                numstat_out = repo.git.diff('--numstat', 'HEAD~1', 'HEAD')
                parse_numstat(numstat_out)
            else:
                # Initial commit (no parents)
                diff_text = repo.git.show(head_commit.hexsha)
                if diff_text:
                    diff_parts.append(diff_text)
                commit_messages = [head_commit.message.strip()]
                # For stats of initial commit, diff against NULL_TREE
                numstat_out = repo.git.diff_tree('--numstat', head_commit.hexsha, root=True)
                parse_numstat(numstat_out)
        except Exception:
            pass

    # 2. Local workspace staged & unstaged changes (to capture the current uncommitted session state)
    try:
        # Unstaged changes in working directory (git diff)
        unstaged_diff = repo.git.diff()
        if unstaged_diff:
            diff_parts.append(unstaged_diff)
            
        # Staged changes in index (git diff --cached)
        staged_diff = repo.git.diff('--cached')
        if staged_diff:
            diff_parts.append(staged_diff)
            
        # Get stats for workspace changes
        wd_numstat = repo.git.diff('--numstat')
        parse_numstat(wd_numstat)
        
        wd_cached_numstat = repo.git.diff('--numstat', '--cached')
        parse_numstat(wd_cached_numstat)
    except Exception:
        pass
        
    git_diff_raw = "\n".join(diff_parts)
    file_change_stats = [
        {"file_path": filepath, "insertions": stats["insertions"], "deletions": stats["deletions"]}
        for filepath, stats in file_stats_map.items()
    ]
    
    return git_diff_raw, commit_messages, file_change_stats
