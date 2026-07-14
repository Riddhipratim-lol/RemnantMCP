import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class SourceType(str, Enum):
    GIT_DIFF = "GIT_DIFF"
    COMMIT = "COMMIT"
    CHAT = "CHAT"
    ERROR_LOG = "ERROR_LOG"
    FILE_CHANGE = "FILE_CHANGE"


@dataclass
class ArtifactObject:
    source_type: SourceType
    project_id: str          # Unique project identifier (UUID string or remote URL hash)
    session_id: str          # Groups artifacts from the same work session
    timestamp: datetime
    raw_content: str         # Verbatim source content
    file_paths: List[str] = field(default_factory=list)    # Affected file paths, if applicable
    metadata: Dict = field(default_factory=dict)           # Source-specific additional fields
    content_hash: Optional[str] = None                     # SHA-256 of raw_content


class MemoryType(str, Enum):
    ARCHITECTURAL_DECISION = "ARCHITECTURAL_DECISION"
    IMPLEMENTATION_RATIONALE = "IMPLEMENTATION_RATIONALE"
    FAILED_APPROACH = "FAILED_APPROACH"
    BUG_RESOLUTION = "BUG_RESOLUTION"
    DESIGN_TRADEOFF = "DESIGN_TRADEOFF"
    COMPONENT_RELATIONSHIP = "COMPONENT_RELATIONSHIP"
    CONSTRAINT = "CONSTRAINT"


@dataclass
class MemoryObject:
    id: uuid.UUID
    project_id: uuid.UUID
    session_id: uuid.UUID
    memory_type: MemoryType
    title: str
    content: str
    rationale: Optional[str] = None
    alternatives_considered: List[str] = field(default_factory=list)
    outcome: Optional[str] = None
    components: List[str] = field(default_factory=list)
    file_paths: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    related_memory_ids: List[uuid.UUID] = field(default_factory=list)
    confidence_score: float = 1.0
    source_artifact_ids: List[uuid.UUID] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    is_superseded: bool = False
    superseded_by: Optional[uuid.UUID] = None
