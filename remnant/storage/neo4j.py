import os
from typing import List, Tuple
from neo4j import GraphDatabase
import uuid

class Neo4jClientManager:
    def __init__(self, uri: str = None, user: str = None, password: str = None):
        self.uri = uri or os.getenv("REMNANT_NEO4J_URL", "bolt://localhost:7687")
        self.user = user or os.getenv("REMNANT_NEO4J_USER", "neo4j")
        self.password = password or os.getenv("REMNANT_NEO4J_PASSWORD", "password")
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        
    def close(self):
        self.driver.close()
        
    def upsert_memory_graph(self, memories: List['MemoryObject'], relationships: List[Tuple[uuid.UUID, 'RelationshipType', uuid.UUID]]):
        with self.driver.session() as session:
            session.execute_write(self._create_nodes_and_edges, memories, relationships)
            
    @staticmethod
    def _create_nodes_and_edges(tx, memories, relationships):
        # Insert memories
        for mem in memories:
            tx.run(
                """
                MERGE (m:Memory {id: $id})
                SET m.type = $type,
                    m.title = $title,
                    m.project_id = $project_id,
                    m.session_id = $session_id,
                    m.timestamp = $timestamp
                """,
                id=str(mem.id),
                type=mem.memory_type.value if hasattr(mem.memory_type, 'value') else mem.memory_type,
                title=mem.title,
                project_id=str(mem.project_id),
                session_id=str(mem.session_id),
                timestamp=mem.created_at.isoformat() if mem.created_at else None
            )
            
            # Link Project
            tx.run(
                """
                MERGE (p:Project {id: $project_id})
                MERGE (m:Memory {id: $id})
                MERGE (p)-[:CONTAINS]->(m)
                """,
                project_id=str(mem.project_id), id=str(mem.id)
            )

            # Link Session
            if mem.session_id:
                tx.run(
                    """
                    MERGE (s:Session {id: $session_id})
                    MERGE (m:Memory {id: $id})
                    MERGE (s)-[:PRODUCED]->(m)
                    """,
                    session_id=str(mem.session_id), id=str(mem.id)
                )

            # Link Components
            for comp in mem.components:
                tx.run(
                    """
                    MERGE (c:Component {name: $comp, project_id: $project_id})
                    MERGE (m:Memory {id: $id})
                    MERGE (m)-[:APPLIES_TO]->(c)
                    """,
                    comp=comp, project_id=str(mem.project_id), id=str(mem.id)
                )

            # Link Files
            for fp in mem.file_paths:
                tx.run(
                    """
                    MERGE (f:File {path: $path, project_id: $project_id})
                    MERGE (m:Memory {id: $id})
                    MERGE (m)-[:TOUCHES]->(f)
                    """,
                    path=fp, project_id=str(mem.project_id), id=str(mem.id)
                )

        # Insert relationships between memories
        for rel in relationships:
            source_id, rel_type, target_id = rel
            rel_type_str = rel_type.value if hasattr(rel_type, 'value') else rel_type
            
            tx.run(
                f"""
                MERGE (s:Memory {{id: $source_id}})
                MERGE (t:Memory {{id: $target_id}})
                MERGE (s)-[:{rel_type_str}]->(t)
                """,
                source_id=str(source_id),
                target_id=str(target_id)
            )
