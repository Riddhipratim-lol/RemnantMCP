"""
Layer 4 — Phase 1: Semantic Search (Qdrant)

Embeds the incoming query with Voyage Code 3, then runs a filtered
vector similarity search against Qdrant Cloud to produce the top-K
candidate memory IDs scoped to the correct project.
"""

from remnant.config import settings
from typing import Dict, List, Optional
import uuid

import voyageai
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models


class QdrantSemanticSearch:
    """
    Wraps Qdrant query_points with Voyage Code 3 query embedding.

    Attributes:
        collection_name: Qdrant collection that holds memory vectors.
        top_k:           Default number of candidates to return.
    """

    COLLECTION_NAME = "memory_vectors"

    def __init__(
        self,
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        voyage_api_key: Optional[str] = None,
        top_k: int = 20,
    ):
        self.top_k = top_k

        # --- Qdrant client ---
        qdrant_url = qdrant_url or settings.remnant_qdrant_url
        qdrant_api_key = qdrant_api_key or settings.remnant_qdrant_api_key
        if qdrant_url:
            self.qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        else:
            # In-memory fallback for tests / local dev without Qdrant Cloud
            self.qdrant = QdrantClient(":memory:")
            self._bootstrap_collection()

        # --- Voyage client ---
        voyage_api_key = voyage_api_key or settings.voyage_api_key
        if not voyage_api_key:
            raise ValueError("VOYAGE_API_KEY environment variable is not set.")
        self.voyage = voyageai.Client(api_key=voyage_api_key)

    def _bootstrap_collection(self) -> None:
        """Create the collection in the in-memory Qdrant instance if absent."""
        collections = self.qdrant.get_collections()
        existing = {c.name for c in collections.collections}
        if self.COLLECTION_NAME not in existing:
            self.qdrant.create_collection(
                collection_name=self.COLLECTION_NAME,
                vectors_config=qdrant_models.VectorParams(
                    size=1024, distance=qdrant_models.Distance.COSINE
                ),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_query(self, query: str) -> List[float]:
        """
        Embed a query string using Voyage Code 3 (query input type).

        Args:
            query: The task description or code snippet to embed.

        Returns:
            A 1024-dimensional embedding vector.
        """
        result = self.voyage.embed(
            [query],
            model="voyage-code-3",
            input_type="query",
        )
        return result.embeddings[0]

    def search(
        self,
        query: str,
        project_id: str,
        top_k: Optional[int] = None,
        filters: Optional[Dict] = None,
    ) -> List[str]:
        """
        Phase 1 entry point: embed the query, filter by project, return top-K
        candidate memory UUIDs.

        Args:
            query:      Task description or active code snippet.
            project_id: UUID string — ensures cross-project isolation.
            top_k:      Override the default candidate count.
            filters:    Optional extra payload conditions, e.g.
                        ``{"component": "auth-service"}`` or
                        ``{"memory_type": "FAILED_APPROACH"}``.

        Returns:
            List of memory UUID strings ordered by descending relevance.
        """
        k = top_k or self.top_k

        # Build Qdrant filter — project_id is mandatory
        must_conditions: List[qdrant_models.Condition] = [
            qdrant_models.FieldCondition(
                key="project_id",
                match=qdrant_models.MatchValue(value=str(project_id)),
            )
        ]

        if filters:
            for field_key, field_val in filters.items():
                if field_val is not None:
                    must_conditions.append(
                        qdrant_models.FieldCondition(
                            key=field_key,
                            match=qdrant_models.MatchValue(value=str(field_val)),
                        )
                    )

        qdrant_filter = qdrant_models.Filter(must=must_conditions)

        query_vector = self.embed_query(query)

        results = self.qdrant.query_points(
            collection_name=self.COLLECTION_NAME,
            query=query_vector,
            query_filter=qdrant_filter,
            limit=k,
        )

        # Extract memory_id from payload (stored as UUID string)
        candidate_ids: List[str] = []
        for point in results.points:
            memory_id = (
                point.payload.get("memory_id") if point.payload else None
            )
            if memory_id:
                candidate_ids.append(memory_id)

        return candidate_ids
