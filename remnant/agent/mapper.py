from typing import Dict, Any, List, Tuple
from uuid import UUID
from remnant.structures import ExtractionState, MemoryType, RelationshipType


def relationship_mapper(state: ExtractionState) -> Dict[str, Any]:
    """
    Evaluates causal linkages between extracted Memory nodes and emits
    Memory-to-Memory relationship edges for Neo4j ingestion.

    Responsibility boundary:
    - THIS FILE (mapper.py):  Memory → Memory causal edges only.
        REJECTED_IN_FAVOR_OF : A failed approach was superseded by a winning choice.
        FIXES                : A bug resolution addresses a constraint or failed approach.
        INFLUENCED           : General causal influence between two memories.

    - neo4j.py (storage layer): Memory → Infrastructure node edges.
        (Memory)-[:APPLIES_TO]->(Component)  — derived from mem.components
        (Memory)-[:TOUCHES]->(File)          — derived from mem.file_paths
        (Session)-[:PRODUCED]->(Memory)      — session provenance
        (Project)-[:CONTAINS]->(Memory)      — project scoping

    APPLIES_TO and TOUCHES are intentionally NOT emitted here. neo4j.py already
    writes them as Memory→Infrastructure edges for every memory. Duplicating them
    as Memory→Memory edges would create graph redundancy and pollute Layer 4
    causal traversal queries with co-location noise.
    """
    resolved_memories = state.get("resolved_memories", [])
    relationships: List[Tuple[UUID, RelationshipType, UUID]] = []

    for i, mem_a in enumerate(resolved_memories):
        for j, mem_b in enumerate(resolved_memories):
            if i >= j:
                continue

            type_a = mem_a.memory_type
            type_b = mem_b.memory_type

            shared_files = set(mem_a.file_paths).intersection(set(mem_b.file_paths))
            shared_components = set(mem_a.components).intersection(set(mem_b.components))
            has_shared_context = bool(shared_files or shared_components)

            # -----------------------------------------------------------------
            # 1. REJECTED_IN_FAVOR_OF
            #    Direction: FAILED_APPROACH → the winning choice
            #    Winning choices: ARCHITECTURAL_DECISION, IMPLEMENTATION_RATIONALE, DESIGN_TRADEOFF
            # -----------------------------------------------------------------
            _winning_types = {
                MemoryType.ARCHITECTURAL_DECISION,
                MemoryType.IMPLEMENTATION_RATIONALE,
                MemoryType.DESIGN_TRADEOFF,
            }
            if has_shared_context:
                if type_a == MemoryType.FAILED_APPROACH and type_b in _winning_types:
                    relationships.append((mem_a.id, RelationshipType.REJECTED_IN_FAVOR_OF, mem_b.id))
                    continue
                if type_b == MemoryType.FAILED_APPROACH and type_a in _winning_types:
                    relationships.append((mem_b.id, RelationshipType.REJECTED_IN_FAVOR_OF, mem_a.id))
                    continue

            # -----------------------------------------------------------------
            # 2. FIXES
            #    Direction: BUG_RESOLUTION → CONSTRAINT or FAILED_APPROACH
            #    A bug fix addresses a known constraint or a previously failed approach.
            # -----------------------------------------------------------------
            if has_shared_context:
                _fixable_types = {MemoryType.CONSTRAINT, MemoryType.FAILED_APPROACH}
                if type_a == MemoryType.BUG_RESOLUTION and type_b in _fixable_types:
                    relationships.append((mem_a.id, RelationshipType.FIXES, mem_b.id))
                    continue
                if type_b == MemoryType.BUG_RESOLUTION and type_a in _fixable_types:
                    relationships.append((mem_b.id, RelationshipType.FIXES, mem_a.id))
                    continue

            # -----------------------------------------------------------------
            # 3. INFLUENCED  (general causal influence — fallback)
            #    Only emit when sharing context AND types make directional sense.
            #
            #    NOTE: APPLIES_TO and TOUCHES are intentionally absent here.
            #    neo4j.py handles both as Memory→Infrastructure edges:
            #      (Memory)-[:APPLIES_TO]->(Component node)
            #      (Memory)-[:TOUCHES]->(File node)
            #    Emitting them as Memory→Memory edges here would create graph
            #    redundancy and pollute Layer 4 causal traversal queries.
            # -----------------------------------------------------------------
            _influence_pairs = {
                # A constraint shaped a decision
                (MemoryType.CONSTRAINT, MemoryType.ARCHITECTURAL_DECISION),
                (MemoryType.CONSTRAINT, MemoryType.IMPLEMENTATION_RATIONALE),
                (MemoryType.CONSTRAINT, MemoryType.DESIGN_TRADEOFF),
                # A decision influenced a rationale
                (MemoryType.ARCHITECTURAL_DECISION, MemoryType.IMPLEMENTATION_RATIONALE),
                # A bug resolution influenced future architectural choices
                (MemoryType.BUG_RESOLUTION, MemoryType.ARCHITECTURAL_DECISION),
                (MemoryType.BUG_RESOLUTION, MemoryType.IMPLEMENTATION_RATIONALE),
                (MemoryType.BUG_RESOLUTION, MemoryType.DESIGN_TRADEOFF),
                # A tradeoff influenced a concrete decision
                (MemoryType.DESIGN_TRADEOFF, MemoryType.ARCHITECTURAL_DECISION),
                # A component relationship influenced an architectural decision
                (MemoryType.COMPONENT_RELATIONSHIP, MemoryType.ARCHITECTURAL_DECISION),
            }
            if has_shared_context:
                if (type_a, type_b) in _influence_pairs:
                    relationships.append((mem_a.id, RelationshipType.INFLUENCED, mem_b.id))
                elif (type_b, type_a) in _influence_pairs:
                    relationships.append((mem_b.id, RelationshipType.INFLUENCED, mem_a.id))

    return {"relationships": relationships}
