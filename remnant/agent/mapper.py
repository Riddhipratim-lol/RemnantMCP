from typing import Dict, Any, List, Tuple
from uuid import UUID
from remnant.structures import ExtractionState, RelationshipType


def relationship_mapper(state: ExtractionState) -> Dict[str, Any]:
    """
    Evaluates dependencies between extracted memories to map causal relationship edges.
    """
    resolved_memories = state.get("resolved_memories", [])
    relationships: List[Tuple[UUID, RelationshipType, UUID]] = []
    
    # Rule-based heuristics for generating relationships between co-extracted memories
    for i, mem_a in enumerate(resolved_memories):
        for j, mem_b in enumerate(resolved_memories):
            if i >= j:
                continue
                
            shared_files = set(mem_a.file_paths).intersection(set(mem_b.file_paths))
            shared_components = set(mem_a.components).intersection(set(mem_b.components))
            
            # If they share context, establish an edge
            if shared_files or shared_components:
                type_a = mem_a.memory_type
                type_b = mem_b.memory_type
                
                if type_a == "FAILED_APPROACH" and type_b == "ARCHITECTURAL_DECISION":
                    relationships.append((mem_a.id, RelationshipType.REJECTED_IN_FAVOR_OF, mem_b.id))
                elif type_b == "FAILED_APPROACH" and type_a == "ARCHITECTURAL_DECISION":
                    relationships.append((mem_b.id, RelationshipType.REJECTED_IN_FAVOR_OF, mem_a.id))
                elif type_a == "BUG_RESOLUTION" and type_b == "CONSTRAINT":
                    relationships.append((mem_a.id, RelationshipType.FIXES, mem_b.id))
                elif type_b == "BUG_RESOLUTION" and type_a == "CONSTRAINT":
                    relationships.append((mem_b.id, RelationshipType.FIXES, mem_a.id))
                else:
                    # Default causal link
                    relationships.append((mem_a.id, RelationshipType.INFLUENCED, mem_b.id))
                    
    return {"relationships": relationships}
