"""
Tests for Layer 4 — Retrieval & Ranking Engine

All external services (Qdrant, Neo4j, Voyage AI, PostgreSQL) are fully
mocked so the test suite runs without any live infrastructure.

Test coverage:
    - QdrantSemanticSearch.search()
    - GraphExpansion.expand() + _traverse() + _fetch_memories()
    - VoyageReranker.rerank() + composite scoring + fallback
    - ContextPacker.pack() + token budget + format
    - PostgresFallbackSearch.search() (tsvector and ILIKE paths)
    - RetrievalCoordinator.retrieve() — happy path, Qdrant failure,
      Neo4j failure, full fallback, post-filtering
"""

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from remnant.structures import MemoryObject, MemoryType, RelationshipType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_memory(
    memory_type: MemoryType = MemoryType.ARCHITECTURAL_DECISION,
    title: str = "Use Redis for caching",
    content: str = "Redis provides sub-millisecond reads.",
    rationale: str = "Need P99 < 100ms for auth checks.",
    components: List[str] = None,
    file_paths: List[str] = None,
    confidence: float = 0.9,
    days_old: int = 30,
) -> MemoryObject:
    """Factory helper to build MemoryObject instances for tests."""
    created = datetime.now(timezone.utc) - timedelta(days=days_old)
    return MemoryObject(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        memory_type=memory_type,
        title=title,
        content=content,
        rationale=rationale,
        components=components or ["cache"],
        file_paths=file_paths or ["src/cache.py"],
        tags=["performance"],
        confidence_score=confidence,
        created_at=created,
        updated_at=created,
    )


PROJECT_ID = str(uuid.uuid4())


# ===========================================================================
# Phase 1 — QdrantSemanticSearch
# ===========================================================================


class TestQdrantSemanticSearch:
    """Unit tests for the Qdrant semantic search phase."""

    @patch("remnant.retrieval.qdrant_search.voyageai.Client")
    @patch("remnant.retrieval.qdrant_search.QdrantClient")
    def test_search_returns_memory_ids(self, MockQdrant, MockVoyage):
        """search() should return memory_id strings from Qdrant payloads."""
        from remnant.retrieval.qdrant_search import QdrantSemanticSearch

        memory_id = str(uuid.uuid4())

        # Mock Qdrant collections list (empty → collection will be created)
        mock_qdrant_instance = MockQdrant.return_value
        mock_qdrant_instance.get_collections.return_value = MagicMock(
            collections=[]
        )

        # Mock search result
        mock_point = MagicMock()
        mock_point.payload = {"memory_id": memory_id, "project_id": PROJECT_ID}
        mock_result = MagicMock()
        mock_result.points = [mock_point]
        mock_qdrant_instance.query_points.return_value = mock_result

        # Mock Voyage embed
        mock_voyage_instance = MockVoyage.return_value
        mock_voyage_instance.embed.return_value = MagicMock(
            embeddings=[[0.1] * 1024]
        )

        searcher = QdrantSemanticSearch(voyage_api_key="test-key")
        result = searcher.search("JWT authentication", PROJECT_ID)

        assert isinstance(result, list)
        assert memory_id in result
        mock_voyage_instance.embed.assert_called_once()
        mock_qdrant_instance.query_points.assert_called_once()

    @patch("remnant.retrieval.qdrant_search.voyageai.Client")
    @patch("remnant.retrieval.qdrant_search.QdrantClient")
    def test_search_with_component_filter(self, MockQdrant, MockVoyage):
        """Extra filters should be appended to the Qdrant must-conditions."""
        from remnant.retrieval.qdrant_search import QdrantSemanticSearch

        mock_qdrant_instance = MockQdrant.return_value
        mock_qdrant_instance.get_collections.return_value = MagicMock(collections=[])
        mock_qdrant_instance.query_points.return_value = MagicMock(points=[])
        MockVoyage.return_value.embed.return_value = MagicMock(
            embeddings=[[0.1] * 1024]
        )

        searcher = QdrantSemanticSearch(voyage_api_key="test-key")
        result = searcher.search(
            "auth check",
            PROJECT_ID,
            filters={"component": "auth-service"},
        )

        assert result == []
        call_kwargs = mock_qdrant_instance.query_points.call_args[1]
        # Verify the filter was passed
        assert call_kwargs["query_filter"] is not None

    @patch("remnant.retrieval.qdrant_search.voyageai.Client")
    @patch("remnant.retrieval.qdrant_search.QdrantClient")
    def test_empty_results_when_no_matches(self, MockQdrant, MockVoyage):
        """search() returns [] when Qdrant returns no matching points."""
        from remnant.retrieval.qdrant_search import QdrantSemanticSearch

        mock_qdrant_instance = MockQdrant.return_value
        mock_qdrant_instance.get_collections.return_value = MagicMock(collections=[])
        mock_qdrant_instance.query_points.return_value = MagicMock(points=[])
        MockVoyage.return_value.embed.return_value = MagicMock(
            embeddings=[[0.0] * 1024]
        )

        searcher = QdrantSemanticSearch(voyage_api_key="test-key")
        result = searcher.search("completely unrelated", PROJECT_ID)
        assert result == []


# ===========================================================================
# Phase 2 — GraphExpansion
# ===========================================================================


class TestGraphExpansion:
    """Unit tests for the Neo4j graph expansion phase."""

    @patch("remnant.retrieval.graph_expansion.PostgresStorage")
    @patch("remnant.retrieval.graph_expansion.GraphDatabase")
    def test_expand_returns_merged_memories(self, MockGDB, MockPG):
        """expand() should merge seed + related IDs and fetch from PostgreSQL."""
        from remnant.retrieval.graph_expansion import GraphExpansion

        seed_id = str(uuid.uuid4())
        related_id = str(uuid.uuid4())
        mem = make_memory()

        mock_driver = MockGDB.driver.return_value
        # Simulate execute_query returning one related memory
        mock_record = MagicMock()
        mock_record.__getitem__ = lambda self, key: related_id
        mock_driver.execute_query.return_value = ([mock_record], MagicMock(), [])

        # Patch _fetch_memories to avoid real DB call
        with patch.object(
            GraphExpansion,
            "_fetch_memories",
            return_value=[mem],
        ) as mock_fetch:
            ge = GraphExpansion(neo4j_uri="bolt://localhost:7687")
            result = ge.expand([seed_id], PROJECT_ID)

        assert isinstance(result, list)
        mock_fetch.assert_called_once()

    @patch("remnant.retrieval.graph_expansion.PostgresStorage")
    @patch("remnant.retrieval.graph_expansion.GraphDatabase")
    def test_expand_empty_seeds(self, MockGDB, MockPG):
        """expand() with no seeds should return empty list without DB calls."""
        from remnant.retrieval.graph_expansion import GraphExpansion

        ge = GraphExpansion(neo4j_uri="bolt://localhost:7687")
        result = ge.expand([], PROJECT_ID)

        assert result == []
        MockGDB.driver.return_value.execute_query.assert_not_called()

    @patch("remnant.retrieval.graph_expansion.PostgresStorage")
    @patch("remnant.retrieval.graph_expansion.GraphDatabase")
    def test_traverse_handles_neo4j_error(self, MockGDB, MockPG):
        """_traverse() should return [] on Neo4j errors (graceful degradation)."""
        from remnant.retrieval.graph_expansion import GraphExpansion

        mock_driver = MockGDB.driver.return_value
        mock_driver.execute_query.side_effect = Exception("Neo4j connection refused")

        ge = GraphExpansion(neo4j_uri="bolt://localhost:7687")
        result = ge._traverse(["some-id"])

        assert result == []


# ===========================================================================
# Phase 3 — VoyageReranker
# ===========================================================================


class TestVoyageReranker:
    """Unit tests for the cross-encoder re-ranker."""

    @patch("remnant.retrieval.reranker.voyageai.Client")
    def test_rerank_returns_sorted_tuples(self, MockVoyage):
        """rerank() should return (MemoryObject, score) tuples sorted desc."""
        from remnant.retrieval.reranker import VoyageReranker

        m1 = make_memory(title="Redis cache", confidence=0.9, days_old=10)
        m2 = make_memory(title="Auth JWT", confidence=0.7, days_old=100)
        m3 = make_memory(title="DB schema", confidence=0.5, days_old=5)

        # Voyage returns: m3 highest relevance, then m1, then m2
        mock_r1 = MagicMock(index=0, relevance_score=0.6)
        mock_r2 = MagicMock(index=1, relevance_score=0.3)
        mock_r3 = MagicMock(index=2, relevance_score=0.9)

        mock_client = MockVoyage.return_value
        mock_client.rerank.return_value = MagicMock(
            results=[mock_r1, mock_r2, mock_r3]
        )

        reranker = VoyageReranker(voyage_api_key="test-key")
        ranked = reranker.rerank("cache strategy", [m1, m2, m3])

        assert len(ranked) == 3
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True), "Scores must be descending"

    @patch("remnant.retrieval.reranker.voyageai.Client")
    def test_composite_score_formula(self, MockVoyage):
        """Composite score should be: reranker*0.7 + confidence*0.15 + recency*0.15."""
        from remnant.retrieval.reranker import VoyageReranker

        mem = make_memory(confidence=0.8, days_old=0)  # brand new → recency ≈ 1.0

        mock_r = MagicMock(index=0, relevance_score=1.0)
        MockVoyage.return_value.rerank.return_value = MagicMock(results=[mock_r])

        reranker = VoyageReranker(voyage_api_key="test-key", recency_lambda=0.01)
        ranked = reranker.rerank("query", [mem])

        _, composite = ranked[0]
        expected_min = (1.0 * 0.70) + (0.8 * 0.15) + (0.95 * 0.15)  # recency ≈ 0.95
        expected_max = (1.0 * 0.70) + (0.8 * 0.15) + (1.0 * 0.15)  # recency = 1.0

        assert expected_min <= composite <= expected_max + 0.01

    @patch("remnant.retrieval.reranker.voyageai.Client")
    def test_fallback_ranking_on_api_error(self, MockVoyage):
        """On Voyage API error, rerank() should fall back to confidence×recency."""
        from remnant.retrieval.reranker import VoyageReranker

        MockVoyage.return_value.rerank.side_effect = Exception("API timeout")

        memories = [make_memory(confidence=0.9), make_memory(confidence=0.5)]
        reranker = VoyageReranker(voyage_api_key="test-key")
        ranked = reranker.rerank("auth", memories)

        assert len(ranked) == 2
        # Higher-confidence memory should rank first in fallback
        assert ranked[0][0].confidence_score >= ranked[1][0].confidence_score

    @patch("remnant.retrieval.reranker.voyageai.Client")
    def test_rerank_empty_memories(self, MockVoyage):
        """rerank() with empty list should return []."""
        from remnant.retrieval.reranker import VoyageReranker

        reranker = VoyageReranker(voyage_api_key="test-key")
        assert reranker.rerank("query", []) == []

    @patch("remnant.retrieval.reranker.voyageai.Client")
    def test_recency_decay_older_memory(self, MockVoyage):
        """A very old memory should have a lower recency score than a new one."""
        from remnant.retrieval.reranker import VoyageReranker

        reranker = VoyageReranker(voyage_api_key="test-key", recency_lambda=0.01)
        old_mem = make_memory(days_old=365)
        new_mem = make_memory(days_old=1)

        old_recency = reranker._recency_score(old_mem)
        new_recency = reranker._recency_score(new_mem)

        assert new_recency > old_recency
        assert old_recency == pytest.approx(math.exp(-0.01 * 365), rel=0.05)


# ===========================================================================
# Phase 4 — ContextPacker
# ===========================================================================


class TestContextPacker:
    """Unit tests for the token-budgeted context packer."""

    def test_pack_single_memory(self):
        """pack() should produce a block with HEADER and FOOTER."""
        from remnant.retrieval.packer import ContextPacker

        packer = ContextPacker(max_tokens=2000)
        mem = make_memory()
        block = packer.pack([(mem, 0.9)])

        assert "=== PROJECT MEMORY CONTEXT ===" in block
        assert "=== END MEMORY CONTEXT ===" in block
        assert mem.title in block

    def test_pack_token_budget_respected(self):
        """With a tiny budget, pack() should include fewer memories."""
        from remnant.retrieval.packer import ContextPacker

        packer = ContextPacker(max_tokens=50)  # very tight
        mems = [(make_memory(title=f"Decision {i}"), 0.9 - i * 0.1) for i in range(10)]
        block = packer.pack(mems)

        # Count how many "Decision N" labels appear
        included_count = sum(f"Decision {i}" in block for i in range(10))
        assert included_count < 10, "Token budget should limit the number of memories"

    def test_pack_empty_memories(self):
        """pack() with empty list should produce a graceful no-memory message."""
        from remnant.retrieval.packer import ContextPacker

        packer = ContextPacker(max_tokens=2000)
        block = packer.pack([])

        assert "No relevant memories found" in block

    def test_pack_failed_approach_format(self):
        """FAILED_APPROACH memories should use 'Attempted:' and 'Abandoned Because:'."""
        from remnant.retrieval.packer import ContextPacker

        packer = ContextPacker(max_tokens=2000)
        mem = make_memory(
            memory_type=MemoryType.FAILED_APPROACH,
            title="Server-side sessions in PostgreSQL",
            rationale="Session table became a write bottleneck at 500 req/s",
        )
        block = packer.pack([(mem, 0.8)])

        assert "Attempted:" in block
        assert "Abandoned Because:" in block

    def test_pack_constraint_format(self):
        """CONSTRAINT memories should use 'Constraint:' and 'Impact:' labels."""
        from remnant.retrieval.packer import ContextPacker

        packer = ContextPacker(max_tokens=2000)
        mem = make_memory(
            memory_type=MemoryType.CONSTRAINT,
            title="P99 latency must be < 100ms",
            content="Rules out any synchronous external verification calls.",
        )
        block = packer.pack([(mem, 0.95)])

        assert "Constraint:" in block
        assert "Impact:" in block

    def test_count_tokens_positive(self):
        """count_tokens() should return a positive integer for non-empty text."""
        from remnant.retrieval.packer import ContextPacker

        packer = ContextPacker()
        count = packer.count_tokens("Hello world, this is a test string.")
        assert isinstance(count, int)
        assert count > 0

    def test_pack_includes_file_paths(self):
        """File paths should appear in the formatted block."""
        from remnant.retrieval.packer import ContextPacker

        packer = ContextPacker(max_tokens=2000)
        mem = make_memory(file_paths=["src/auth/jwt_handler.py"])
        block = packer.pack([(mem, 0.9)])

        assert "src/auth/jwt_handler.py" in block


# ===========================================================================
# PostgresFallbackSearch
# ===========================================================================


class TestPostgresFallbackSearch:
    """Unit tests for the PostgreSQL full-text search fallback."""

    @patch("remnant.retrieval.fallback.PostgresStorage")
    def test_search_calls_ts_search_first(self, MockPG):
        """search() should attempt tsvector search before ILIKE fallback."""
        from remnant.retrieval.fallback import PostgresFallbackSearch

        fallback = PostgresFallbackSearch.__new__(PostgresFallbackSearch)
        # Mock the internal search methods
        fallback._ts_search = MagicMock(return_value=[make_memory()])
        fallback._ilike_search = MagicMock(return_value=[])
        fallback.pg = MagicMock()

        result = fallback.search("query", PROJECT_ID)

        fallback._ts_search.assert_called_once()
        fallback._ilike_search.assert_not_called()
        assert len(result) == 1

    @patch("remnant.retrieval.fallback.PostgresStorage")
    def test_search_falls_back_to_ilike_on_ts_error(self, MockPG):
        """If tsvector search throws, search() should try ILIKE."""
        from remnant.retrieval.fallback import PostgresFallbackSearch

        mem = make_memory()
        fallback = PostgresFallbackSearch.__new__(PostgresFallbackSearch)
        fallback._ts_search = MagicMock(side_effect=Exception("tsvector error"))
        fallback._ilike_search = MagicMock(return_value=[mem])
        fallback.pg = MagicMock()

        result = fallback.search("query", PROJECT_ID)

        fallback._ilike_search.assert_called_once()
        assert result == [mem]

    @patch("remnant.retrieval.fallback.PostgresStorage")
    def test_search_returns_empty_on_both_failures(self, MockPG):
        """If both tsvector and ILIKE fail, search() returns []."""
        from remnant.retrieval.fallback import PostgresFallbackSearch

        fallback = PostgresFallbackSearch.__new__(PostgresFallbackSearch)
        fallback._ts_search = MagicMock(side_effect=Exception("ts error"))
        fallback._ilike_search = MagicMock(side_effect=Exception("ilike error"))
        fallback.pg = MagicMock()

        result = fallback.search("query", PROJECT_ID)

        assert result == []


# ===========================================================================
# RetrievalCoordinator — Integration-style unit tests
# ===========================================================================


class TestRetrievalCoordinator:
    """Unit tests for the full retrieval pipeline coordinator."""

    def _make_coordinator(self):
        """Build a coordinator with all sub-components mocked."""
        from remnant.retrieval.coordinator import RetrievalCoordinator

        coord = RetrievalCoordinator.__new__(RetrievalCoordinator)
        coord.top_k_semantic = 20
        coord.max_tokens = 2000

        from remnant.retrieval.packer import ContextPacker
        coord._packer = ContextPacker(max_tokens=2000)

        coord._semantic = MagicMock()
        coord._graph = MagicMock()
        coord._reranker = MagicMock()
        coord._fallback = MagicMock()

        return coord

    def test_happy_path_returns_context_block(self):
        """Full pipeline should return a non-empty context block."""
        coord = self._make_coordinator()

        mem = make_memory()
        seed_id = str(mem.id)

        coord._semantic.search.return_value = [seed_id]
        coord._graph.expand.return_value = [mem]
        coord._reranker.rerank.return_value = [(mem, 0.85)]

        result = coord.retrieve("JWT auth check", PROJECT_ID)

        assert result.context_block
        assert "=== PROJECT MEMORY CONTEXT ===" in result.context_block
        assert result.fallback_used is False
        assert result.retrieved_count == 1

    def test_qdrant_failure_triggers_fallback(self):
        """If semantic search fails, fallback search should be used."""
        coord = self._make_coordinator()
        mem = make_memory()

        coord._semantic.search.side_effect = Exception("Qdrant offline")
        coord._fallback.search.return_value = [mem]
        coord._reranker.rerank.return_value = [(mem, 0.7)]

        result = coord.retrieve("cache strategy", PROJECT_ID)

        assert result.fallback_used is True
        coord._fallback.search.assert_called_once()

    def test_neo4j_failure_degrades_gracefully(self):
        """If graph expansion fails, pipeline should still return results."""
        coord = self._make_coordinator()

        seed_id = str(uuid.uuid4())
        mem = make_memory()

        coord._semantic.search.return_value = [seed_id]
        coord._graph.expand.side_effect = Exception("Neo4j timeout")
        # The fallback _fetch_memories path
        coord._graph._fetch_memories = MagicMock(return_value=[mem])
        coord._reranker.rerank.return_value = [(mem, 0.75)]

        result = coord.retrieve("auth service", PROJECT_ID)

        # Should not raise; context block should still be formed
        assert result.context_block
        assert "=== PROJECT MEMORY CONTEXT ===" in result.context_block

    def test_post_filter_by_memory_type(self):
        """_post_filter should remove memories that don't match requested types."""
        from remnant.retrieval.coordinator import RetrievalCoordinator

        memories = [
            make_memory(memory_type=MemoryType.ARCHITECTURAL_DECISION),
            make_memory(memory_type=MemoryType.FAILED_APPROACH),
            make_memory(memory_type=MemoryType.CONSTRAINT),
        ]

        filtered = RetrievalCoordinator._post_filter(
            memories,
            memory_types=["FAILED_APPROACH"],
            file_path=None,
        )

        assert len(filtered) == 1
        assert filtered[0].memory_type == MemoryType.FAILED_APPROACH

    def test_post_filter_by_file_path(self):
        """_post_filter should retain only memories with matching file paths."""
        from remnant.retrieval.coordinator import RetrievalCoordinator

        m1 = make_memory(file_paths=["src/auth/jwt.py"])
        m2 = make_memory(file_paths=["src/cache/redis.py"])

        filtered = RetrievalCoordinator._post_filter(
            [m1, m2],
            memory_types=None,
            file_path="auth",
        )

        assert len(filtered) == 1
        assert filtered[0] is m1

    def test_retrieve_no_backends_returns_error_block(self):
        """With all backends offline, retrieve() should return an error context block."""
        coord = self._make_coordinator()
        coord._semantic = None
        coord._fallback = None

        result = coord.retrieve("something", PROJECT_ID)

        assert "unavailable" in result.context_block.lower()
        assert result.fallback_used is True

    def test_retrieve_respects_max_tokens_override(self):
        """max_tokens kwarg should override the coordinator default."""
        coord = self._make_coordinator()

        mem = make_memory()
        coord._semantic.search.return_value = [str(mem.id)]
        coord._graph.expand.return_value = [mem]
        coord._reranker.rerank.return_value = [(mem, 0.9)]

        # Very small budget
        result = coord.retrieve("query", PROJECT_ID, max_tokens=10)

        # Even with tiny budget, we should still get header/footer
        assert "PROJECT MEMORY CONTEXT" in result.context_block
