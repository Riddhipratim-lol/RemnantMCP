"""
Layer 4 — Retrieval Coordinator

Orchestrates the three-phase hybrid retrieval pipeline and assembles
the final context block:

    Phase 1 → QdrantSemanticSearch   (semantic candidates)
    Phase 2 → GraphExpansion          (causally related expansion)
    Phase 3 → VoyageReranker          (cross-encoder re-ranking)
    Phase 4 → ContextPacker           (token-budgeted formatting)
    Fallback→ PostgresFallbackSearch  (Qdrant/Neo4j offline resilience)

Called from Layer 5 (MCP Tool: recall_context) as the single entry
point for all retrieval operations.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from remnant.retrieval.fallback import PostgresFallbackSearch
from remnant.retrieval.graph_expansion import GraphExpansion
from remnant.retrieval.packer import ContextPacker
from remnant.retrieval.qdrant_search import QdrantSemanticSearch
from remnant.retrieval.reranker import VoyageReranker
from remnant.structures import MemoryObject


@dataclass
class RetrievalResult:
    """
    Structured output from the retrieval coordinator.

    Attributes:
        context_block:    Formatted, token-budgeted memory context string.
        memory_ids:       UUIDs of memories that were included in the block.
        retrieved_count:  Total number of memories evaluated before packing.
        fallback_used:    True when Qdrant/Neo4j were unavailable.
    """

    context_block: str
    memory_ids: List[str] = field(default_factory=list)
    retrieved_count: int = 0
    fallback_used: bool = False


class RetrievalCoordinator:
    """
    High-level orchestrator for Layer 4 retrieval.

    Handles initialisation of each sub-component, orchestrates the
    three retrieval phases in sequence, and degrades gracefully when
    vector / graph stores are offline.

    Args:
        qdrant_url:       Qdrant Cloud endpoint URL.
        qdrant_api_key:   Qdrant Cloud API key.
        neo4j_uri:        Neo4j bolt:// or neo4j+s:// URI.
        neo4j_user:       Neo4j username.
        neo4j_password:   Neo4j password.
        voyage_api_key:   Voyage AI API key.
        db_url:           PostgreSQL connection string.
        top_k_semantic:   Candidates to retrieve from Qdrant (default: 20).
        max_tokens:       Default context token budget (default: 2 000).
    """

    def __init__(
        self,
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        neo4j_uri: Optional[str] = None,
        neo4j_user: Optional[str] = None,
        neo4j_password: Optional[str] = None,
        voyage_api_key: Optional[str] = None,
        db_url: Optional[str] = None,
        top_k_semantic: int = 20,
        max_tokens: int = 2_000,
    ):
        self.top_k_semantic = top_k_semantic
        self.max_tokens = max_tokens

        # Lazily initialise each component; set to None if init fails
        self._semantic: Optional[QdrantSemanticSearch] = None
        self._graph: Optional[GraphExpansion] = None
        self._reranker: Optional[VoyageReranker] = None
        self._fallback: Optional[PostgresFallbackSearch] = None
        self._packer = ContextPacker(max_tokens=max_tokens)

        # --- Semantic search (Phase 1) ---
        try:
            self._semantic = QdrantSemanticSearch(
                qdrant_url=qdrant_url,
                qdrant_api_key=qdrant_api_key,
                voyage_api_key=voyage_api_key,
                top_k=top_k_semantic,
            )
        except Exception as exc:
            print(f"[RetrievalCoordinator] QdrantSemanticSearch init failed: {exc}")

        # --- Graph expansion (Phase 2) ---
        try:
            self._graph = GraphExpansion(
                neo4j_uri=neo4j_uri,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_password,
                db_url=db_url,
            )
        except Exception as exc:
            print(f"[RetrievalCoordinator] GraphExpansion init failed: {exc}")

        # --- Reranker (Phase 3) ---
        try:
            self._reranker = VoyageReranker(voyage_api_key=voyage_api_key)
        except Exception as exc:
            print(f"[RetrievalCoordinator] VoyageReranker init failed: {exc}")

        # --- PostgreSQL fallback ---
        try:
            self._fallback = PostgresFallbackSearch(db_url=db_url)
        except Exception as exc:
            print(f"[RetrievalCoordinator] PostgresFallbackSearch init failed: {exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        project_id: str,
        component: Optional[str] = None,
        file_path: Optional[str] = None,
        memory_types: Optional[List[str]] = None,
        max_tokens: Optional[int] = None,
        top_k: Optional[int] = None,
    ) -> RetrievalResult:
        """
        Full three-phase retrieval pipeline.

        Args:
            query:        Task description or active code snippet.
            project_id:   UUID string for project-scoped isolation.
            component:    Optional component name filter (e.g. "auth-service").
            file_path:    Optional file path filter.
            memory_types: Optional list of MemoryType strings to filter.
            max_tokens:   Override the coordinator-level token budget.
            top_k:        Override the default Qdrant top-K candidate count.

        Returns:
            RetrievalResult with formatted context block and metadata.
        """
        budget = max_tokens if max_tokens is not None else self.max_tokens
        k = top_k if top_k is not None else self.top_k_semantic

        use_fallback = self._semantic is None
        candidates: List[MemoryObject] = []

        if not use_fallback:
            try:
                # Build optional filters for Qdrant payload
                qdrant_filters: Dict = {}
                if component:
                    qdrant_filters["component"] = component
                if memory_types and len(memory_types) == 1:
                    qdrant_filters["memory_type"] = memory_types[0]

                # Phase 1 — Semantic search
                seed_ids = self._semantic.search(
                    query=query,
                    project_id=project_id,
                    top_k=k,
                    filters=qdrant_filters or None,
                )

                # Phase 2 — Graph expansion
                if self._graph is not None and seed_ids:
                    try:
                        candidates = self._graph.expand(
                            seed_memory_ids=seed_ids,
                            project_id=project_id,
                        )
                    except Exception as exc:
                        print(f"[RetrievalCoordinator] Graph expansion failed: {exc}")
                        # Graceful degradation: use seed IDs fetched from PG directly
                        candidates = self._graph._fetch_memories(seed_ids, project_id)
                else:
                    # Neo4j offline: resolve seed memories from PostgreSQL directly
                    if self._fallback:
                        candidates = self._fallback.search(
                            query=query,
                            project_id=project_id,
                            top_k=k,
                        )

            except Exception as exc:
                print(f"[RetrievalCoordinator] Semantic search failed: {exc}")
                use_fallback = True

        if use_fallback or not candidates:
            use_fallback = True
            if self._fallback:
                # Filter by first memory_type if provided
                mt = memory_types[0] if memory_types else None
                candidates = self._fallback.search(
                    query=query,
                    project_id=project_id,
                    top_k=k,
                    memory_type=mt,
                )
            else:
                return RetrievalResult(
                    context_block=(
                        "=== PROJECT MEMORY CONTEXT ===\n\n"
                        "(Retrieval unavailable — all storage backends offline.)\n\n"
                        "=== END MEMORY CONTEXT ==="
                    ),
                    fallback_used=True,
                )

        # Apply optional post-retrieval filters (memory_type, file_path)
        candidates = self._post_filter(candidates, memory_types, file_path)

        # Phase 3 — Re-ranking
        if self._reranker is not None and candidates:
            ranked = self._reranker.rerank(query=query, memories=candidates)
        else:
            # Fallback rank: preserve order with neutral score
            ranked = [(m, m.confidence_score) for m in candidates]

        # Phase 4 — Context packing
        context_block = self._packer.pack(ranked, max_tokens=budget)

        # Extract the IDs of memories that made it into the context block
        included_ids = [str(m.id) for m, _ in ranked[: len(ranked)]]

        return RetrievalResult(
            context_block=context_block,
            memory_ids=included_ids,
            retrieved_count=len(candidates),
            fallback_used=use_fallback,
        )

    def close(self) -> None:
        """Release underlying database connections."""
        if self._graph is not None:
            try:
                self._graph.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _post_filter(
        memories: List[MemoryObject],
        memory_types: Optional[List[str]],
        file_path: Optional[str],
    ) -> List[MemoryObject]:
        """
        Apply optional client-side filters to the expanded candidate set.
        Qdrant payload filtering handles this at query time when possible,
        but multi-type and file_path filtering needs post-processing.
        """
        result = memories

        if memory_types:
            mt_set = set(memory_types)
            result = [
                m for m in result
                if (
                    m.memory_type.value
                    if hasattr(m.memory_type, "value")
                    else m.memory_type
                ) in mt_set
            ]

        if file_path:
            result = [
                m for m in result
                if any(file_path in fp for fp in m.file_paths)
            ]

        return result
