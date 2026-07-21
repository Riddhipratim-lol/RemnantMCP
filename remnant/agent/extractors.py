import os
from typing import Any, Dict, List
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain.chat_models import init_chat_model
from remnant.structures import ArtifactObject


class ExtractedMemory(BaseModel):
    memory_type: str = Field(description="One of: ARCHITECTURAL_DECISION, IMPLEMENTATION_RATIONALE, FAILED_APPROACH, BUG_RESOLUTION, DESIGN_TRADEOFF, COMPONENT_RELATIONSHIP, CONSTRAINT")
    title: str = Field(description="Short summary")
    content: str = Field(description="Detailed explanation")
    rationale: str = Field(default="", description="The 'why' behind the memory")
    alternatives_considered: List[str] = Field(default_factory=list, description="Alternatives evaluated")
    outcome: str = Field(default="", description="What ultimately happened")
    components: List[str] = Field(default_factory=list, description="Logical component names")
    file_paths: List[str] = Field(default_factory=list, description="Affected file paths")
    tags: List[str] = Field(default_factory=list, description="Searchable tags")


class ExtractionResult(BaseModel):
    memories: List[ExtractedMemory]


def _get_llm():
    return init_chat_model(
        "google_genai:gemini-3.1-flash-lite",
        temperature=0.1
    ).with_structured_output(ExtractionResult)


def _process_extraction(state: dict, system_prompt: str, user_prompt: str) -> dict:
    artifact = state.get("artifact")
    if not artifact:
        return {"raw_memories": []}
    
    # Handle both ArtifactObject instance and dict
    raw_content = getattr(artifact, "raw_content", None)
    if raw_content is None and isinstance(artifact, dict):
        raw_content = artifact.get("raw_content", "")
    
    if not raw_content:
        return {"raw_memories": []}
        
    llm = _get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", user_prompt)
    ])
    chain = prompt | llm
    
    try:
        result = chain.invoke({"content": raw_content})
        raw_mems = [m.model_dump() for m in result.memories] if result and hasattr(result, 'memories') else []
    except Exception as e:
        print(f"Extraction error: {e}")
        raw_mems = []
    
    # Safely extract artifact_id
    artifact_id = getattr(artifact, "id", None)
    if not artifact_id and isinstance(artifact, dict):
        artifact_id = artifact.get("id")
        
    for rm in raw_mems:
        rm["source_artifact_ids"] = [artifact_id] if artifact_id else []
        
    return {"raw_memories": raw_mems}


def code_extract(state: dict) -> dict:
    """Extracts memories from git diffs, commits, and file changes."""
    return _process_extraction(
        state,
        "You are an expert software architect analyzing code changes. Extract architectural decisions, implementation rationales, and bug resolutions. Output ONLY valid structured data.",
        "Analyze this code change and extract structured memories.\n\nContent:\n{content}"
    )


def chat_extractor(state: dict) -> dict:
    """Extracts memories from chat transcripts."""
    return _process_extraction(
        state,
        "You are an expert engineering manager analyzing chat transcripts. Extract architectural decisions, constraints, failed approaches, and design tradeoffs. Output ONLY valid structured data.",
        "Analyze this transcript and extract structured memories.\n\nContent:\n{content}"
    )


def error_extractor(state: dict) -> dict:
    """Extracts memories from error logs."""
    return _process_extraction(
        state,
        "You are an expert debugging assistant analyzing error logs. Extract bug resolutions, constraints, and failed approaches. Output ONLY valid structured data.",
        "Analyze these logs and extract structured memories.\n\nContent:\n{content}"
    )
