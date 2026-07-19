import os
from typing import List
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from remnant.structures import MemoryObject

class QdrantClientManager:
    def __init__(self, url: str = None, api_key: str = None):
        self.url = url or os.getenv("REMNANT_QDRANT_URL")
        self.api_key = api_key or os.getenv("REMNANT_QDRANT_API_KEY")
        self.collection_name = "memory_vectors"
        
        # Local fallback if no URL
        if self.url:
            self.client = QdrantClient(url=self.url, api_key=self.api_key)
        else:
            self.client = QdrantClient(":memory:")
            
        self.ensure_collection_exists()

    def ensure_collection_exists(self):
        try:
            collections = self.client.get_collections()
            if not any(c.name == self.collection_name for c in collections.collections):
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=1024, distance=Distance.COSINE)
                )
        except Exception as e:
            print(f"Qdrant collection creation error: {e}")
            raise e

    def upsert_memories(self, memories: List[MemoryObject], embeddings: List[List[float]]) -> None:
        if not memories or not embeddings or len(memories) != len(embeddings):
            raise ValueError("Mismatched memories and embeddings count")

        points = []
        for mem, emb in zip(memories, embeddings):
            payload = {
                "memory_id": str(mem.id),
                "project_id": str(mem.project_id),
                "memory_type": mem.memory_type.value if hasattr(mem.memory_type, 'value') else mem.memory_type,
                "component": mem.components[0] if mem.components else None,  # Assuming primary component
                "file_paths": mem.file_paths,
                "session_id": str(mem.session_id),
                "timestamp": mem.created_at.isoformat() if mem.created_at else None,
                "confidence_score": mem.confidence_score
            }
            # Qdrant requires integer or UUID string for point ID.
            points.append(
                PointStruct(
                    id=str(mem.id),
                    vector=emb,
                    payload=payload
                )
            )
            
        try:
            self.client.upsert(
                collection_name=self.collection_name,
                points=points
            )
        except Exception as e:
            print(f"Qdrant upsert error: {e}")
            raise e
