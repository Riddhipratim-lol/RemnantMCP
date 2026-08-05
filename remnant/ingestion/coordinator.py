from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional
from remnant.structures import ArtifactObject, SourceType
from remnant.storage.postgres import PostgresStorage
from remnant.ingestion.git_parser import parse_git_repo
from remnant.ingestion.redactor import redact_content
from remnant.ingestion.grouper import SessionGrouper

# Maximum characters per chat artifact chunk fed to the extraction LLM.
#
# Basis: extraction LLM is google_genai:gemini-3.5-flash-lite which has a
# 1 048 576 token context window.  At ~4 chars/token:
#   500 000 chars ≈ 125 000 tokens  (leaves ~875k tokens for prompts + output)
#
# This means virtually no real-world coding session will ever be chunked.
# Chunking is still applied as a safety net for pathologically long inputs
# (e.g. automated test transcripts, multi-day session dumps).
_TRANSCRIPT_CHUNK_MAX_CHARS: int = 500_000
# Characters of overlap between consecutive chunks to avoid cutting mid-thought.
_TRANSCRIPT_CHUNK_OVERLAP: int = 500

class IngestionCoordinator:
    def __init__(self, db_storage: Optional[PostgresStorage] = None, window_hours: int = 4):
        self.db_storage = db_storage or PostgresStorage()
        self.grouper = SessionGrouper(db_storage=self.db_storage, window_hours=window_hours)

    @staticmethod
    def _chunk_transcript(
        text: str,
        max_chars: int = _TRANSCRIPT_CHUNK_MAX_CHARS,
        overlap: int = _TRANSCRIPT_CHUNK_OVERLAP,
    ) -> List[str]:
        """
        Split a long chat transcript into overlapping chunks so each chunk
        can be sent to the extraction LLM independently without exceeding
        its context window.

        Strategy
        --------
        - If ``text`` fits within ``max_chars``, returns it as a single-element list
          (no splitting — preserves existing behaviour for normal-length sessions).
        - Otherwise, slides a window of size ``max_chars`` over the text, advancing
          by ``(max_chars - overlap)`` characters each step.
        - Each window boundary is nudged to the next newline character so that
          chunks don't cut mid-sentence.

        Args:
            text:      The raw transcript string.
            max_chars: Maximum number of characters per chunk (default 40 000).
            overlap:   Characters of overlap between consecutive chunks (default 500).

        Returns:
            List of non-empty string chunks.  Always at least one element.
        """
        if len(text) <= max_chars:
            return [text]

        chunks: List[str] = []
        step = max_chars - overlap
        start = 0
        while start < len(text):
            end = start + max_chars
            if end < len(text):
                # Nudge the boundary to the next newline so we don't split mid-line.
                newline_pos = text.find("\n", end)
                if newline_pos != -1 and (newline_pos - end) < overlap:
                    end = newline_pos + 1  # include the newline in this chunk
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start += step

        return chunks if chunks else [text]

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
            
        # Chat transcript artifact — fall back to session_notes when no full
        # transcript is provided so notes are never silently discarded.
        chat_content = chat_transcript if (chat_transcript and chat_transcript.strip()) else None
        notes_content = session_notes if (session_notes and session_notes.strip()) else None
        raw_chat_content = chat_content or notes_content
        if raw_chat_content:
            is_notes_fallback = chat_content is None
            chunks = self._chunk_transcript(raw_chat_content)
            total_chunks = len(chunks)
            for i, chunk in enumerate(chunks):
                raw_artifacts.append({
                    "source_type": SourceType.CHAT,
                    "raw_content": chunk,
                    "timestamp": timestamp,
                    "metadata": {
                        "session_notes": session_notes,
                        "is_notes_fallback": is_notes_fallback,
                        # Chunk provenance — useful for debugging and future dedup
                        "chunk_index": i,
                        "total_chunks": total_chunks,
                    },
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
