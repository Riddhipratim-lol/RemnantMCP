import uuid
import subprocess
from typing import Dict, Any, List
from remnant.structures import ExtractionState, MemoryObject, MemoryType


def get_git_files(project_path: str = ".") -> List[str]:
    """Retrieve tracked files using git ls-files for fuzzy matching."""
    try:
        result = subprocess.run(
            ["git", "-C", project_path, "ls-files"],
            capture_output=True, text=True, check=True
        )
        return result.stdout.splitlines()
    except subprocess.CalledProcessError:
        return []


def resolve_entities(state: ExtractionState) -> Dict[str, Any]:
    """
    Resolves extracted entity names (files/components) to real paths,
    and instantiates full MemoryObject records.
    """
    raw_memories = state.get("raw_memories", [])
    project_id_str = state.get("project_id", "")
    session_id_str = state.get("session_id", "")
    
    # Try to parse UUIDs or generate temporary ones if they are not standard UUID strings
    try:
        project_id = uuid.UUID(project_id_str)
    except ValueError:
        project_id = uuid.uuid4()
        
    try:
        session_id = uuid.UUID(session_id_str)
    except ValueError:
        session_id = uuid.uuid4()

    project_root = state.get("project_root") or "."
    project_files = get_git_files(project_root)
    resolved_memories = []
    
    for raw in raw_memories:
        mem_id = uuid.uuid4()
        
        # Fuzzy match file paths
        raw_files = raw.get("file_paths", [])
        resolved_files = []
        for rf in raw_files:
            matches = [f for f in project_files if rf in f or f in rf]
            if matches:
                resolved_files.append(matches[0])
            else:
                resolved_files.append(rf)
                
        # Enforce valid MemoryType enum
        try:
            mem_type = MemoryType(raw.get("memory_type", ""))
        except ValueError:
            mem_type = MemoryType.IMPLEMENTATION_RATIONALE
            
        mem = MemoryObject(
            id=mem_id,
            project_id=project_id,
            session_id=session_id,
            memory_type=mem_type,
            title=raw.get("title", "Untitled Memory"),
            content=raw.get("content", ""),
            rationale=raw.get("rationale"),
            alternatives_considered=raw.get("alternatives_considered", []),
            outcome=raw.get("outcome"),
            components=raw.get("components", []),
            file_paths=resolved_files,
            tags=raw.get("tags", []),
            source_artifact_ids=raw.get("source_artifact_ids", [])
        )
        resolved_memories.append(mem)
        
    return {"resolved_memories": resolved_memories}
