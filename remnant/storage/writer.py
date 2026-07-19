from typing import List, Tuple, Dict, Any
import uuid
from remnant.storage.postgres import PostgresStorage
from remnant.storage.voyage import VoyageClient
from remnant.storage.qdrant import QdrantClientManager
from remnant.storage.neo4j import Neo4jClientManager
from remnant.structures import MemoryObject, RelationshipType

class FanOutWriter:
    def __init__(self, db_url=None, qdrant_url=None, neo4j_url=None):
        self.pg = PostgresStorage(db_url)
        
        # Initialize clients lazily or handle optional missing keys gracefully
        self.voyage = None
        self.qdrant = None
        self.neo4j = None
        
        try:
            self.voyage = VoyageClient()
        except Exception as e:
            print(f"Voyage init skipped/failed: {e}")
            
        try:
            self.qdrant = QdrantClientManager(url=qdrant_url)
        except Exception as e:
            print(f"Qdrant init skipped/failed: {e}")
            
        try:
            self.neo4j = Neo4jClientManager(uri=neo4j_url)
        except Exception as e:
            print(f"Neo4j init skipped/failed: {e}")

    def write_memories(self, memories: List[MemoryObject], relationships: List[Tuple[uuid.UUID, RelationshipType, uuid.UUID]]) -> Dict[str, Any]:
        results = {
            "postgres": False,
            "qdrant": False,
            "neo4j": False,
            "errors": []
        }
        
        if not memories:
            return results

        # 1. Primary Store: PostgreSQL (Hard dependency, rolls back on error)
        try:
            self.pg.insert_memory_batch(memories, relationships)
            results["postgres"] = True
        except Exception as e:
            results["errors"].append(f"Postgres write failed: {e}")
            return results # Stop here, do not write to secondary stores

        # Prepare texts for embedding
        texts = []
        for mem in memories:
            title = mem.title or ""
            content = mem.content or ""
            rationale = mem.rationale or ""
            text_to_embed = f"Title: {title}\nContent: {content}\nRationale: {rationale}"
            texts.append(text_to_embed)

        # 2. Semantic Store: Qdrant (Soft dependency)
        if self.voyage and self.qdrant:
            try:
                embeddings = self.voyage.generate_embeddings(texts)
                self.qdrant.upsert_memories(memories, embeddings)
                results["qdrant"] = True
            except Exception as e:
                print(f"Qdrant/Voyage write failed: {e}")
                results["errors"].append(f"Qdrant/Voyage error: {e}")

        # 3. Graph Store: Neo4j (Soft dependency)
        if self.neo4j:
            try:
                self.neo4j.upsert_memory_graph(memories, relationships)
                results["neo4j"] = True
            except Exception as e:
                print(f"Neo4j write failed: {e}")
                results["errors"].append(f"Neo4j error: {e}")

        return results
