import os
from typing import Any, Dict, List
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
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
    return ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite",
        temperature=0.1
    ).with_structured_output(ExtractionResult)


def code_extract(state: dict) -> dict:
    """Extracts memories from git diffs, commits, and file changes."""
    artifact: ArtifactObject = state["artifact"]
    llm = _get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an expert software architect analyzing code changes. Extract architectural decisions, implementation rationales, and bug resolutions. Output ONLY valid structured data."),
        ("user", "Analyze this code change and extract structured memories.\n\nContent:\n{content}")
    ])
    chain = prompt | llm
    result = chain.invoke({"content": artifact.raw_content})
    
    raw_mems = [m.model_dump() for m in result.memories]
    for rm in raw_mems:
        if hasattr(artifact, "id"):
            rm["source_artifact_ids"] = [artifact.id]
        else:
            rm["source_artifact_ids"] = []
    return {"raw_memories": raw_mems}


def chat_extractor(state: dict) -> dict:
    """Extracts memories from chat transcripts."""
    artifact: ArtifactObject = state["artifact"]
    llm = _get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an expert engineering manager analyzing chat transcripts. Extract architectural decisions, constraints, failed approaches, and design tradeoffs. Output ONLY valid structured data."),
        ("user", "Analyze this transcript and extract structured memories.\n\nContent:\n{content}")
    ])
    chain = prompt | llm
    result = chain.invoke({"content": artifact.raw_content})
    
    raw_mems = [m.model_dump() for m in result.memories]
    for rm in raw_mems:
        if hasattr(artifact, "id"):
            rm["source_artifact_ids"] = [artifact.id]
        else:
            rm["source_artifact_ids"] = []
    return {"raw_memories": raw_mems}


def error_extractor(state: dict) -> dict:
    """Extracts memories from error logs."""
    artifact: ArtifactObject = state["artifact"]
    llm = _get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an expert debugging assistant analyzing error logs. Extract bug resolutions, constraints, and failed approaches. Output ONLY valid structured data."),
        ("user", "Analyze these logs and extract structured memories.\n\nContent:\n{content}")
    ])
    chain = prompt | llm
    result = chain.invoke({"content": artifact.raw_content})
    
    raw_mems = [m.model_dump() for m in result.memories]
    for rm in raw_mems:
        if hasattr(artifact, "id"):
            rm["source_artifact_ids"] = [artifact.id]
        else:
            rm["source_artifact_ids"] = []
    return {"raw_memories": raw_mems}
