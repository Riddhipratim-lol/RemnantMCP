"""
Layer 4 — Phase 3: Re-Ranking (Voyage rerank-2.5-lite)

Submits (query, memory_content) pairs to the Voyage cross-encoder
reranker, then blends the relevance score with a recency decay and
the agent extraction confidence to produce a final composite rank.

Composite score formula (as specified in Implementation.md):
    score = (reranker_score × 0.70) + (confidence × 0.15) + (recency × 0.15)

where:
    recency = exp(−λ × days_since_creation)
    λ       = 0.01  (configurable via RECENCY_DECAY_LAMBDA)
"""

import math
import os
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import voyageai

from remnant.structures import MemoryObject


# Default exponential decay rate (λ).  Lower → slower decay.
_DEFAULT_LAMBDA: float = 0.01


class VoyageReranker:
    """
    Re-ranks a list of MemoryObjects against a query using Voyage rerank-2.5-lite.

    Args:
        voyage_api_key:    Overrides VOYAGE_API_KEY env variable.
        model:             Voyage reranker model name.
        recency_lambda:    Exponential decay constant for recency scoring.
    """

    def __init__(
        self,
        voyage_api_key: Optional[str] = None,
        model: str = "rerank-2.5-lite",
        recency_lambda: float = _DEFAULT_LAMBDA,
    ):
        api_key = voyage_api_key or os.getenv("VOYAGE_API_KEY")
        if not api_key:
            raise ValueError("VOYAGE_API_KEY environment variable is not set.")
        self.client = voyageai.Client(api_key=api_key)
        self.model = model
        self.recency_lambda = recency_lambda

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rerank(
        self,
        query: str,
        memories: List[MemoryObject],
        top_n: Optional[int] = None,
    ) -> List[Tuple[MemoryObject, float]]:
        """
        Phase 3 entry point: score each (query, memory) pair, blend with
        recency and confidence, return sorted list.

        Args:
            query:    The original user query / task description.
            memories: Expanded candidate set from Phase 2.
            top_n:    Optionally trim the result to the top N entries.

        Returns:
            List of (MemoryObject, composite_score) tuples, highest score first.
        """
        if not memories:
            return []

        # Build document strings for the reranker
        documents = [self._format_document(m) for m in memories]

        try:
            result = self.client.rerank(
                query=query,
                documents=documents,
                model=self.model,
                top_k=len(documents),  # score all; we apply top_n ourselves
                return_documents=False,
                truncation=True,
            )
        except Exception as exc:
            print(f"[VoyageReranker] Rerank API error: {exc}. Falling back to confidence ranking.")
            return self._fallback_rank(memories, top_n)

        # Build index → raw relevance score map
        score_map: dict = {r.index: r.relevance_score for r in result.results}

        ranked: List[Tuple[MemoryObject, float]] = []
        for idx, mem in enumerate(memories):
            relevance = score_map.get(idx, 0.0)
            recency = self._recency_score(mem)
            confidence = float(mem.confidence_score or 0.5)

            composite = (relevance * 0.70) + (confidence * 0.15) + (recency * 0.15)
            ranked.append((mem, composite))

        ranked.sort(key=lambda x: x[1], reverse=True)

        if top_n is not None:
            ranked = ranked[:top_n]

        return ranked

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _format_document(self, mem: MemoryObject) -> str:
        """
        Concatenate the memory's most informative fields into a single string
        for the cross-encoder.  Keeps it concise to stay within token budgets.
        """
        parts = []
        if mem.title:
            parts.append(f"Title: {mem.title}")
        if mem.content:
            parts.append(f"Content: {mem.content}")
        if mem.rationale:
            parts.append(f"Rationale: {mem.rationale}")
        if mem.components:
            parts.append(f"Components: {', '.join(mem.components)}")
        return "\n".join(parts)

    def _recency_score(self, mem: MemoryObject) -> float:
        """
        Compute an exponential decay score in [0, 1] based on how many days
        have passed since the memory was created.

        score = exp(−λ × days_since_creation)
        """
        if mem.created_at is None:
            return 0.5  # neutral fallback

        now = datetime.now(timezone.utc)
        created = mem.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        days = max(0.0, (now - created).total_seconds() / 86_400)
        return math.exp(-self.recency_lambda * days)

    def _fallback_rank(
        self,
        memories: List[MemoryObject],
        top_n: Optional[int],
    ) -> List[Tuple[MemoryObject, float]]:
        """
        Used when the Voyage reranker is unavailable.  Ranks purely on
        confidence × recency so the pipeline degrades gracefully.
        """
        ranked = []
        for mem in memories:
            recency = self._recency_score(mem)
            confidence = float(mem.confidence_score or 0.5)
            # Equal weights when no relevance signal is available
            composite = (confidence * 0.50) + (recency * 0.50)
            ranked.append((mem, composite))

        ranked.sort(key=lambda x: x[1], reverse=True)
        if top_n is not None:
            ranked = ranked[:top_n]
        return ranked
