from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional
from remnant.structures import ArtifactObject, SourceType
from remnant.storage.postgres import PostgresStorage
from remnant.ingestion.git_parser import parse_git_repo
from remnant.ingestion.redactor import redact_content
from remnant.ingestion.grouper import SessionGrouper

class IngestionCoordinator:
    def __init__(self, db_storage: Optional[PostgresStorage] = None, window_hours: int = 4):
        self.db_storage = db_storage or PostgresStorage()
        self.grouper = SessionGrouper(db_storage=self.db_storage, window_hours=window_hours)

    def ingest_session(
        self,
        repo_path: str,
        project_id: str,
        chat_transcript: Optional[str] = None,
        logs: Optional[str] = None,
        session_notes: Optional[str] = None,
        commit_sha: Optional[str] = None,
        custom_redaction_rules: Optional[List[str]] = None
    ) -> Tuple[str, List[ArtifactObject]]:
        """
        Orchestrate the ingestion of all session artifacts.
        
        Args:
            repo_path: Absolute path to the repository.
            project_id: The project identifier.
            chat_transcript: Optional end-of-session chat transcript.
            logs: Optional logs or terminal output.
            session_notes: Optional notes or context.
            commit_sha: Optional current/target commit SHA.
            custom_redaction_rules: Optional custom regexes for redaction.
            
        Returns:
            session_id (str): The active session UUID.
            artifacts (List[ArtifactObject]): List of newly ingested and deduplicated artifacts.
        """
        # 1. Resolve target project ID (UUID format)
        project_uuid = self.db_storage.get_or_create_project(
            project_id=project_id,
            name=project_id.split("/")[-1] if "/" in project_id else project_id,
            repo_path=repo_path
        )
        
        # 2. Get last processed SHA from db to determine incremental range
        last_processed_sha = self.db_storage.get_last_processed_sha(project_uuid)
        
        # 3. Parse Git repository changes
        git_diff_raw, commit_messages, file_change_stats = parse_git_repo(
            repo_path=repo_path,
            last_processed_sha=last_processed_sha
        )
        
        # Determine the commit SHA to log for this ingestion run
        # If commit_sha isn't provided, try to find current HEAD commit sha from repo
        current_sha = commit_sha
        if not current_sha:
            try:
                import git
                repo = git.Repo(repo_path)
                if not repo.bare and repo.head.is_valid():
                    current_sha = repo.head.commit.hexsha
            except Exception:
                pass

        # 4. Formulate raw artifacts list
        raw_artifacts = []
        timestamp = datetime.now(timezone.utc)
        
        # Git diff artifact
        if git_diff_raw.strip():
            raw_artifacts.append({
                "source_type": SourceType.GIT_DIFF,
                "raw_content": git_diff_raw,
                "file_paths": [stat["file_path"] for stat in file_change_stats],
                "timestamp": timestamp,
                "metadata": {"file_stats": file_change_stats}
            })
            
        # Commit messages artifact
        if commit_messages:
            combined_commits = "\n---\n".join(commit_messages)
            raw_artifacts.append({
                "source_type": SourceType.COMMIT,
                "raw_content": combined_commits,
                "timestamp": timestamp,
                "metadata": {"commits": commit_messages}
            })
            
        # File change summary artifact
        if file_change_stats:
            stats_lines = []
            file_paths = []
            for stat in file_change_stats:
                stats_lines.append(f"{stat['file_path']}: +{stat['insertions']} -{stat['deletions']}")
                file_paths.append(stat['file_path'])
            combined_stats = "\n".join(stats_lines)
            raw_artifacts.append({
                "source_type": SourceType.FILE_CHANGE,
                "raw_content": combined_stats,
                "file_paths": file_paths,
                "timestamp": timestamp,
                "metadata": {"file_stats": file_change_stats}
            })
            
        # Chat transcript artifact
        if chat_transcript and chat_transcript.strip():
            raw_artifacts.append({
                "source_type": SourceType.CHAT,
                "raw_content": chat_transcript,
                "timestamp": timestamp,
                "metadata": {"session_notes": session_notes}
            })
            
        # Error log / logs artifact
        if logs and logs.strip():
            raw_artifacts.append({
                "source_type": SourceType.ERROR_LOG,
                "raw_content": logs,
                "timestamp": timestamp,
                "metadata": {}
            })
            
        # 5. Redact secrets in all raw content fields
        for raw in raw_artifacts:
            raw["raw_content"] = redact_content(raw["raw_content"], custom_redaction_rules)
            
        # 6. Group under session, check deduplication, and log in DB
        session_id, final_artifacts = self.grouper.process_and_group(
            raw_artifacts=raw_artifacts,
            project_id=project_uuid,
            commit_sha=current_sha
        )
        
        return session_id, final_artifacts
