import pytest
from unittest.mock import MagicMock, patch
import uuid
from datetime import datetime, timezone
from remnant.structures import MemoryObject, MemoryType, RelationshipType
from remnant.storage.writer import FanOutWriter

@pytest.fixture
def mock_memory():
    return MemoryObject(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        memory_type=MemoryType.ARCHITECTURAL_DECISION,
        title="Use Redis for Cache",
        content="Redis provides faster access",
        rationale="Latency must be under 100ms",
        components=["cache", "backend"],
        file_paths=["src/cache.py"],
        tags=["performance"],
        confidence_score=0.95
    )

def test_fan_out_writer_success(mock_memory):
    with patch('remnant.storage.writer.PostgresStorage') as MockPG, \
         patch('remnant.storage.writer.VoyageClient') as MockVoyage, \
         patch('remnant.storage.writer.QdrantClientManager') as MockQdrant, \
         patch('remnant.storage.writer.Neo4jClientManager') as MockNeo4j:
         
        mock_pg_instance = MockPG.return_value
        mock_voyage_instance = MockVoyage.return_value
        mock_qdrant_instance = MockQdrant.return_value
        mock_neo4j_instance = MockNeo4j.return_value
        
        mock_voyage_instance.generate_embeddings.return_value = [[0.1, 0.2, 0.3]]
        
        writer = FanOutWriter()
        
        memories = [mock_memory]
        relationships = []
        
        results = writer.write_memories(memories, relationships)
        
        assert results["postgres"] is True
        assert results["qdrant"] is True
        assert results["neo4j"] is True
        assert len(results["errors"]) == 0
        
        mock_pg_instance.insert_memory_batch.assert_called_once_with(memories, relationships)
        mock_qdrant_instance.upsert_memories.assert_called_once_with(memories, [[0.1, 0.2, 0.3]])
        mock_neo4j_instance.upsert_memory_graph.assert_called_once_with(memories, relationships)

def test_fan_out_writer_postgres_failure(mock_memory):
    with patch('remnant.storage.writer.PostgresStorage') as MockPG, \
         patch('remnant.storage.writer.VoyageClient') as MockVoyage, \
         patch('remnant.storage.writer.QdrantClientManager') as MockQdrant, \
         patch('remnant.storage.writer.Neo4jClientManager') as MockNeo4j:
         
        mock_pg_instance = MockPG.return_value
        mock_pg_instance.insert_memory_batch.side_effect = Exception("DB Connection Error")
        
        writer = FanOutWriter()
        
        results = writer.write_memories([mock_memory], [])
        
        assert results["postgres"] is False
        assert results["qdrant"] is False
        assert results["neo4j"] is False
        assert len(results["errors"]) == 1
        assert "Postgres write failed" in results["errors"][0]
        
        assert not MockQdrant.return_value.upsert_memories.called
        assert not MockNeo4j.return_value.upsert_memory_graph.called

def test_fan_out_writer_secondary_soft_failure(mock_memory):
    with patch('remnant.storage.writer.PostgresStorage') as MockPG, \
         patch('remnant.storage.writer.VoyageClient') as MockVoyage, \
         patch('remnant.storage.writer.QdrantClientManager') as MockQdrant, \
         patch('remnant.storage.writer.Neo4jClientManager') as MockNeo4j:
         
        mock_voyage_instance = MockVoyage.return_value
        mock_qdrant_instance = MockQdrant.return_value
        mock_neo4j_instance = MockNeo4j.return_value
        
        mock_voyage_instance.generate_embeddings.return_value = [[0.1, 0.2, 0.3]]
        
        mock_qdrant_instance.upsert_memories.side_effect = Exception("Qdrant Timeout")
        
        writer = FanOutWriter()
        
        results = writer.write_memories([mock_memory], [])
        
        assert results["postgres"] is True
        assert results["qdrant"] is False
        assert results["neo4j"] is True
        assert len(results["errors"]) == 1
        assert "Qdrant/Voyage error" in results["errors"][0]
