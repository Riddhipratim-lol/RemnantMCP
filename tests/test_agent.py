import pytest
import uuid
from datetime import datetime, timezone
from remnant.structures import (
    ArtifactObject,
    SourceType,
    ExtractionState,
    MemoryObject,
    MemoryType,
    RelationshipType
)
from remnant.agent.router import route_artifacts
from remnant.agent.resolver import resolve_entities
from remnant.agent.mapper import relationship_mapper
from remnant.agent.validator import validator_node, check_validation
from remnant.agent.graph import build_graph

def test_route_artifacts():
    # Setup state with different artifact types
    art_code = ArtifactObject(
        source_type=SourceType.GIT_DIFF,
        project_id="test",
        session_id="test",
        timestamp=datetime.now(timezone.utc),
        raw_content="diff"
    )
    art_chat = ArtifactObject(
        source_type=SourceType.CHAT,
        project_id="test",
        session_id="test",
        timestamp=datetime.now(timezone.utc),
        raw_content="chat"
    )
    
    state = ExtractionState(artifacts=[art_code, art_chat])
    sends = route_artifacts(state)
    
    assert len(sends) == 2
    assert sends[0].node == "code_extract"
    assert sends[1].node == "chat_extractor"

def test_resolve_entities():
    # Provide raw memories and mock get_git_files by patching it or just testing UUID generation
    state = ExtractionState(
        raw_memories=[
            {
                "memory_type": "ARCHITECTURAL_DECISION",
                "title": "Use Redis",
                "content": "Use redis for caching",
                "file_paths": ["non_existent_file.py"]
            }
        ],
        project_id="12345678-1234-5678-1234-567812345678",
        session_id="invalid-uuid-string"
    )
    
    result = resolve_entities(state)
    resolved = result["resolved_memories"]
    
    assert len(resolved) == 1
    mem = resolved[0]
    assert isinstance(mem.id, uuid.UUID)
    assert mem.memory_type == MemoryType.ARCHITECTURAL_DECISION
    assert mem.title == "Use Redis"
    # test parsing UUID
    assert str(mem.project_id) == "12345678-1234-5678-1234-567812345678"
    # test fallback UUID creation for invalid string
    assert isinstance(mem.session_id, uuid.UUID)

def test_relationship_mapper():
    mem1 = MemoryObject(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        memory_type=MemoryType.FAILED_APPROACH,
        title="Failed DB",
        content="Failed",
        components=["Database"]
    )
    mem2 = MemoryObject(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        memory_type=MemoryType.ARCHITECTURAL_DECISION,
        title="Success DB",
        content="Success",
        components=["Database"]
    )
    
    state = ExtractionState(resolved_memories=[mem1, mem2])
    result = relationship_mapper(state)
    
    relationships = result["relationships"]
    assert len(relationships) == 1
    rel = relationships[0]
    # FAILED_APPROACH and ARCHITECTURAL_DECISION sharing a component -> REJECTED_IN_FAVOR_OF
    assert rel[1] == RelationshipType.REJECTED_IN_FAVOR_OF
    assert rel[0] == mem1.id
    assert rel[2] == mem2.id

def test_validator_node():
    mem_valid = MemoryObject(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        memory_type=MemoryType.ARCHITECTURAL_DECISION,
        title="Valid Title",
        content="Valid content length.",
        rationale="Valid rationale for decision."
    )
    mem_invalid = MemoryObject(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        memory_type=MemoryType.ARCHITECTURAL_DECISION,
        title="No",
        content="Short"
    )
    
    state = ExtractionState(resolved_memories=[mem_valid, mem_invalid], retry_count=0)
    result = validator_node(state)
    
    assert len(result["validation_errors"]) > 0
    assert result["retry_count"] == 1
    assert len(result["final_memories"]) == 1
    assert result["final_memories"][0].id == mem_valid.id

def test_check_validation():
    state_retry = ExtractionState(validation_errors=["Error 1"])
    state_pass = ExtractionState(validation_errors=[])
    
    assert check_validation(state_retry) == "artifact_router"
    assert check_validation(state_pass) == "memory_writer"

def test_build_graph():
    graph = build_graph()
    assert graph is not None
