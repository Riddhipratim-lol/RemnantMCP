import operator
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Dict, List, Optional, Tuple, TypedDict


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
    id: uuid.UUID = field(default_factory=uuid.uuid4)      # Unique artifact identifier for provenance
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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_superseded: bool = False
    superseded_by: Optional[uuid.UUID] = None


class RelationshipType(str, Enum):
    INFLUENCED = "INFLUENCED"
    REJECTED_IN_FAVOR_OF = "REJECTED_IN_FAVOR_OF"
    FIXES = "FIXES"
    APPLIES_TO = "APPLIES_TO"
    TOUCHES = "TOUCHES"
    PRODUCED = "PRODUCED"


class ExtractionState(TypedDict):
    session_id: str
    project_id: str
    project_root: str
    artifacts: List[ArtifactObject]
    classified_artifacts: Annotated[List[Dict[str, Any]], operator.add]
    raw_memories: Annotated[List[Dict[str, Any]], operator.add]
    resolved_memories: Annotated[List[MemoryObject], operator.add]
    relationships: Annotated[List[Tuple[uuid.UUID, RelationshipType, uuid.UUID]], operator.add]
    validation_errors: List[str]          # Overwrite semantics: replaced each validator run
    retry_count: int
    final_memories: Annotated[List[MemoryObject], operator.add]
    storage_results: Dict[str, Any]


