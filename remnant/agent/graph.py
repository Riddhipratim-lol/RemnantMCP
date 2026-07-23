from langgraph.graph import StateGraph, START, END
from remnant.structures import ExtractionState
from remnant.agent.router import route_artifacts
from remnant.agent.extractors import code_extract, chat_extractor, error_extractor
from remnant.agent.resolver import resolve_entities
from remnant.agent.mapper import relationship_mapper
from remnant.agent.validator import validator_node, check_validation
from remnant.storage.writer import FanOutWriter


def artifact_router_node(state: ExtractionState):
    """Dummy passthrough node to anchor the conditional edge for fanning out."""
    return {}


def memory_writer_node(state: ExtractionState):
    """
    Layer 3 (Unified Memory Store) writer.
    Fans out final_memories to PostgreSQL, Qdrant, and Neo4j via FanOutWriter.
    PostgreSQL is a hard dependency (rolls back on failure).
    Qdrant and Neo4j are soft dependencies (failures are logged, not fatal).
    """
    final_memories = state.get("final_memories", [])
    relationships = state.get("relationships", [])

    if not final_memories:
        return {
            "storage_results": {
                "status": "skipped",
                "memories_written": 0,
                "postgres": False,
                "qdrant": False,
                "neo4j": False,
                "errors": ["No validated memories to write."]
            }
        }

    try:
        writer = FanOutWriter()
        results = writer.write_memories(final_memories, relationships)
        results["memories_written"] = len(final_memories)
        results["status"] = "success" if results.get("postgres") else "partial_failure"
    except Exception as e:
        results = {
            "status": "error",
            "memories_written": 0,
            "postgres": False,
            "qdrant": False,
            "neo4j": False,
            "errors": [str(e)]
        }

    return {"storage_results": results}


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

    # Fan out to extractors dynamically via Send API.
    # No path_map required when routing function returns List[Send].
    builder.add_conditional_edges("artifact_router", route_artifacts)

    # Fan in to resolver
    builder.add_edge("code_extract", "entity_resolver")
    builder.add_edge("chat_extractor", "entity_resolver")
    builder.add_edge("error_extractor", "entity_resolver")

    # Linear flow
    builder.add_edge("entity_resolver", "relationship_mapper")
    builder.add_edge("relationship_mapper", "validator")

    # Validation conditional edge (retry loop or proceed to write)
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
