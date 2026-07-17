import pytest
from unittest.mock import MagicMock, patch
import uuid
from remnant.ingestion.coordinator import IngestionCoordinator
from remnant.agent.graph import build_graph
from remnant.structures import ExtractionState, MemoryType
from remnant.agent.extractors import ExtractionResult, ExtractedMemory

@patch('remnant.agent.extractors._get_llm')
def test_layer1_to_layer2_connection(mock_get_llm):
    # Mock the LLM to return a predictable structured output
    mock_llm = MagicMock()
    mock_chain = MagicMock()
    
    # Setup mock LLM structured output
    mock_result = ExtractionResult(memories=[
        ExtractedMemory(
            memory_type=MemoryType.ARCHITECTURAL_DECISION.value,
            title="Use Redis",
            content="Used Redis for caching due to speed.",
            rationale="Needed high performance.",
            components=["Cache"]
        )
    ])
    mock_chain.invoke.return_value = mock_result
    
    # The prompt | llm pipeline is mocked here
    mock_get_llm.return_value = mock_llm
    
    # Mock prompt | llm behavior by mocking __or__ (the pipe operator)
    # Actually, since prompt | llm returns a RunnableSequence, we can patch ChatPromptTemplate.__or__
    # But it's easier to patch the specific extractor function's internal chain, or just patch _get_llm.
    pass # we'll patch invoke later below

@patch('remnant.agent.extractors.ChatPromptTemplate')
@patch('remnant.agent.extractors._get_llm')
def test_end_to_end_data_flow(mock_get_llm, mock_prompt):
    # Setup the mock LLM chain behavior
    mock_chain = MagicMock()
    mock_result = ExtractionResult(memories=[
        ExtractedMemory(
            memory_type=MemoryType.ARCHITECTURAL_DECISION.value,
            title="Use Redis",
            content="Used Redis for caching due to speed.",
            rationale="Needed high performance.",
            components=["Cache"]
        )
    ])
    mock_chain.invoke.return_value = mock_result
    
    # When prompt | llm happens, return mock_chain
    mock_prompt.from_messages.return_value.__or__.return_value = mock_chain

    # Mock Postgres to avoid actual DB hits in the test
    mock_db = MagicMock()
    test_project_uuid = str(uuid.uuid4())
    mock_db.get_or_create_project.return_value = test_project_uuid
    mock_db.get_last_processed_sha.return_value = None
    mock_db.is_hash_processed.return_value = False
    mock_db.get_active_session.return_value = str(uuid.uuid4())

    
    coordinator = IngestionCoordinator(db_storage=mock_db, window_hours=4)
    
    # ==========================================
    # LAYER 1: Ingestion
    # ==========================================
    session_id, final_artifacts = coordinator.ingest_session(
        repo_path=".", 
        project_id="test_project",
        chat_transcript="Developer: Let's use Redis.",
        commit_sha="mock_sha"
    )
    
    assert session_id is not None
    assert len(final_artifacts) > 0
    
    # ==========================================
    # CONNECTION POINT
    # ==========================================
    # Ensure Layer 1 output seamlessly matches Layer 2 State expectation
    state = ExtractionState(
        session_id=str(session_id),
        project_id=test_project_uuid,
        artifacts=final_artifacts
    )
    
    # ==========================================
    # LAYER 2: Agent Execution
    # ==========================================
    graph = build_graph()
    
    # Invoke the graph with the state from Layer 1
    final_state = graph.invoke(state)
    
    # Verify the state successfully traversed and accumulated results
    assert "final_memories" in final_state
    
    memories = final_state["final_memories"]
    assert len(memories) > 0
    
    # Verify the extracted memory made it through the resolver and mapper to final output
    mem = memories[0]
    assert mem.title == "Use Redis"
    assert mem.memory_type == MemoryType.ARCHITECTURAL_DECISION
    
    # Verify storage stub was hit
    assert "storage_results" in final_state
    assert final_state["storage_results"]["status"] == "success"
    assert final_state["storage_results"]["memories_written"] == len(memories)
