from typing import Dict, Any
from remnant.structures import ExtractionState


def validator_node(state: ExtractionState) -> Dict[str, Any]:
    """
    Inspects extracted memory schemas for missing mandatory fields.
    Increments retry count if deficient. If retries are exhausted, accepts with lower confidence.
    """
    resolved_memories = state.get("resolved_memories", [])
    retry_count = state.get("retry_count", 0)
    
    errors = []
    final_memories = []
    
    for mem in resolved_memories:
        mem_errors = []
        if not mem.title or len(mem.title) < 5:
            mem_errors.append(f"Memory {mem.id} has missing or too short title.")
        if not mem.content or len(mem.content) < 10:
            mem_errors.append(f"Memory {mem.id} has missing or too short content.")
        if mem.memory_type in ("ARCHITECTURAL_DECISION", "DESIGN_TRADEOFF", "IMPLEMENTATION_RATIONALE"):
            if not mem.rationale:
                mem_errors.append(f"Memory {mem.id} of type {mem.memory_type} requires a rationale.")
                
        if mem_errors:
            errors.extend(mem_errors)
        else:
            final_memories.append(mem)
            
    # If there are errors but retry_count >= 3, accept anyway with lower confidence
    if errors and retry_count >= 3:
        for mem in resolved_memories:
            mem.confidence_score = 0.5
        final_memories = resolved_memories
        errors = []  # Clear errors so it routes to the writer
        
    return {
        "validation_errors": errors,
        "final_memories": final_memories,
        "retry_count": retry_count + 1
    }


def check_validation(state: ExtractionState) -> str:
    """
    Conditional edge function routing to writer or back to extractors via router.
    """
    errors = state.get("validation_errors", [])
    if len(errors) > 0:
        return "artifact_router"  # Routes back to fan out
    return "memory_writer"
