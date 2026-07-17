from langgraph.graph import StateGraph, START, END
from remnant.structures import ExtractionState
from remnant.agent.router import route_artifacts
from remnant.agent.extractors import code_extract, chat_extractor, error_extractor
from remnant.agent.resolver import resolve_entities
from remnant.agent.mapper import relationship_mapper
from remnant.agent.validator import validator_node, check_validation


def artifact_router_node(state: ExtractionState):
    """Dummy passthrough node to anchor the conditional edge for fanning out."""
    return {}


def memory_writer_node(state: ExtractionState):
    """
    Stub for Layer 3 (Unified Memory Store).
    Persists final_memories to Postgres, Qdrant, Neo4j.
    """
    return {
        "storage_results": {
            "status": "success", 
            "memories_written": len(state.get("final_memories", []))
        }
    }


def build_graph():
    builder = StateGraph(ExtractionState)
    
    # Add nodes
    builder.add_node("artifact_router", artifact_router_node)
    builder.add_node("code_extract", code_extract)
    builder.add_node("chat_extractor", chat_extractor)
    builder.add_node("error_extractor", error_extractor)
    builder.add_node("entity_resolver", resolve_entities)
    builder.add_node("relationship_mapper", relationship_mapper)
    builder.add_node("validator", validator_node)
    builder.add_node("memory_writer", memory_writer_node)
    
    # Build edges
    builder.add_edge(START, "artifact_router")
    
    # Fan out to extractors dynamically
    builder.add_conditional_edges(
        "artifact_router", 
        route_artifacts, 
        ["code_extract", "chat_extractor", "error_extractor"]
    )
    
    # Fan in to resolver
    builder.add_edge("code_extract", "entity_resolver")
    builder.add_edge("chat_extractor", "entity_resolver")
    builder.add_edge("error_extractor", "entity_resolver")
    
    # Linear flow
    builder.add_edge("entity_resolver", "relationship_mapper")
    builder.add_edge("relationship_mapper", "validator")
    
    # Validation conditional edge (Loop or Proceed)
    builder.add_conditional_edges(
        "validator", 
        check_validation, 
        {
            "artifact_router": "artifact_router",
            "memory_writer": "memory_writer"
        }
    )
    
    # Finish
    builder.add_edge("memory_writer", END)
    
    return builder.compile()
