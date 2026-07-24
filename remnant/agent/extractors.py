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


# ---------------------------------------------------------------------------
# Shared memory-type guide injected into every extractor prompt
# ---------------------------------------------------------------------------
_MEMORY_TYPE_GUIDE = """
You extract engineering knowledge into exactly these seven memory types — extract ALL types that are present, never skip a type if evidence exists:

1. ARCHITECTURAL_DECISION — A deliberate structural or system-design choice.
   Signals: "we chose X over Y", "switched from X to Y", major module/library/framework selections, database schema choices, API design patterns.

2. IMPLEMENTATION_RATIONALE — Why a specific coding pattern, library call, or implementation detail was chosen.
   Signals: "used X because", "this approach handles", inline reasoning about code style or algorithm choice.

3. FAILED_APPROACH — Something tried and abandoned with the reason.
   Signals: "tried X but", "abandoned", "reverted", "didn't work because", past-tense descriptions of discarded paths, removed/commented-out code blocks.

4. BUG_RESOLUTION — A defect fix tied to its root cause.
   Signals: "fixed", "root cause was", "the issue was", error traces followed by solutions, guard clauses added after a failure.

5. DESIGN_TRADEOFF — Explicitly weighed options where pros and cons on both sides are acknowledged.
   Signals: "tradeoff", "at the cost of", "sacrificed X for Y", dual-sided analysis, acceptance of a known downside.

6. COMPONENT_RELATIONSHIP — A dependency or interaction boundary between two system parts.
   Signals: "depends on", "calls into", "triggered by", import changes, service-to-service references, module ownership statements.

7. CONSTRAINT — A hard limitation or requirement that bounds future decisions.
   Signals: "must", "cannot", "required to", compatibility requirements, SLA/performance targets, compliance rules, version pins.

Rules:
- A single artifact may yield multiple memories of DIFFERENT types — extract ALL that are present.
- Do NOT collapse two distinct insights into one memory; keep them separate.
- Every memory must have a clear, human-readable title and enough content to stand alone out of context.
- Output ONLY valid structured data matching the schema. No prose outside the schema.
"""


def code_extract(state: dict) -> dict:
    """Extracts all seven memory types from git diffs, commits, and file changes."""
    system_prompt = (
        "You are an expert software architect analyzing code changes (git diffs, commit messages, file modifications).\n"
        + _MEMORY_TYPE_GUIDE
        + "\nSource-specific hints for code artifacts:\n"
        "- ARCHITECTURAL_DECISION: Module structure choices, framework/library selections visible in imports or package changes, API design patterns, database schema migrations.\n"
        "- IMPLEMENTATION_RATIONALE: Comments in diffs explaining why code was written a certain way, non-obvious algorithmic choices, docstring reasoning.\n"
        "- FAILED_APPROACH: Removed/reverted code blocks, commented-out alternatives, TODO/FIXME comments referencing abandoned paths, deleted files.\n"
        "- BUG_RESOLUTION: Fixes visible in diffs — what line changed and why, error handling additions, guard clauses, null checks added after observed failures.\n"
        "- DESIGN_TRADEOFF: One approach chosen while another is explicitly acknowledged in comments or commit messages.\n"
        "- COMPONENT_RELATIONSHIP: Import changes, new service calls, dependency additions in requirements files, interface implementations, new foreign keys.\n"
        "- CONSTRAINT: New linting rules, type annotations enforcing contracts, version pins in requirements, compatibility guard comments.\n"
    )
    user_prompt = (
        "Analyze this code change and extract ALL structured memories present. "
        "Look for all seven memory types, not just the obvious ones.\n\n"
        "Content:\n{content}"
    )
    return _process_extraction(state, system_prompt, user_prompt)


def chat_extractor(state: dict) -> dict:
    """Extracts all seven memory types from chat transcripts and session notes."""
    system_prompt = (
        "You are an expert engineering knowledge distiller analyzing a conversation between a developer and an AI assistant.\n"
        + _MEMORY_TYPE_GUIDE
        + "\nSource-specific hints for chat transcripts:\n"
        "- ARCHITECTURAL_DECISION: Moments where the developer or AI commits to a structural choice (\"let's go with X\", \"we'll use Y instead\").\n"
        "- IMPLEMENTATION_RATIONALE: Explanations of why a specific coding approach was suggested or accepted during discussion.\n"
        "- FAILED_APPROACH: Ideas proposed but explicitly rejected mid-conversation, approaches tried before the chat that the developer describes as failures.\n"
        "- BUG_RESOLUTION: Bug descriptions followed by agreed-upon root cause analyses and fixes, debugging sessions with conclusions.\n"
        "- DESIGN_TRADEOFF: Back-and-forth analysis where pros/cons of multiple options are discussed before settling on one.\n"
        "- COMPONENT_RELATIONSHIP: References to how services, modules, or files interact — even if mentioned informally in conversation.\n"
        "- CONSTRAINT: Requirements, deadlines, compatibility limits, or rules the developer explicitly states as non-negotiable or given.\n"
    )
    user_prompt = (
        "Analyze this chat transcript and extract ALL structured memories present. "
        "Look for all seven memory types throughout the conversation.\n\n"
        "Content:\n{content}"
    )
    return _process_extraction(state, system_prompt, user_prompt)


def error_extractor(state: dict) -> dict:
    """Extracts all seven memory types from error logs, terminal output, and stack traces."""
    system_prompt = (
        "You are an expert debugging assistant analyzing error logs, stack traces, and terminal output.\n"
        + _MEMORY_TYPE_GUIDE
        + "\nSource-specific hints for error logs and terminal output:\n"
        "- ARCHITECTURAL_DECISION: Infrastructure or config choices revealed by errors (e.g., a specific server type chosen, connection strategy adopted as a result).\n"
        "- IMPLEMENTATION_RATIONALE: Error handling patterns, retry strategies, or fallback logic visible in log sequences.\n"
        "- FAILED_APPROACH: Repeated failures of the same operation that reveal an approach that fundamentally did not work.\n"
        "- BUG_RESOLUTION: The specific error + the fix applied — both root cause and resolution must be captured together.\n"
        "- DESIGN_TRADEOFF: Cases where a workaround was chosen due to an environmental constraint, explicitly trading off correctness or performance.\n"
        "- COMPONENT_RELATIONSHIP: Which service/module caused the error and which upstream component called it; dependency chains visible in stack traces.\n"
        "- CONSTRAINT: System limits revealed by errors — memory limits, timeouts, API rate limits, version incompatibilities, permission boundaries.\n"
    )
    user_prompt = (
        "Analyze these logs and extract ALL structured memories present. "
        "Look for all seven memory types, not just bugs.\n\n"
        "Content:\n{content}"
    )
    return _process_extraction(state, system_prompt, user_prompt)
