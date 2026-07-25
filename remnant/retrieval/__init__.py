"""
remnant.retrieval — Layer 4: Retrieval & Ranking Engine

Public surface:
    RetrievalCoordinator  — orchestrates all three retrieval phases
    RetrievalResult       — structured output dataclass
    QdrantSemanticSearch  — Phase 1: semantic search
    GraphExpansion        — Phase 2: graph expansion
    VoyageReranker        — Phase 3: cross-encoder re-ranking
    ContextPacker         — Phase 4: token-budgeted context formatting
    PostgresFallbackSearch — Fallback: PostgreSQL full-text search
"""

from remnant.retrieval.coordinator import RetrievalCoordinator, RetrievalResult
from remnant.retrieval.fallback import PostgresFallbackSearch
from remnant.retrieval.graph_expansion import GraphExpansion
from remnant.retrieval.packer import ContextPacker
from remnant.retrieval.qdrant_search import QdrantSemanticSearch
from remnant.retrieval.reranker import VoyageReranker

__all__ = [
    "RetrievalCoordinator",
    "RetrievalResult",
    "QdrantSemanticSearch",
    "GraphExpansion",
    "VoyageReranker",
    "ContextPacker",
    "PostgresFallbackSearch",
]
