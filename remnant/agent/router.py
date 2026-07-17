from typing import List
from langgraph.types import Send
from remnant.structures import ExtractionState, SourceType, ArtifactObject


def route_artifacts(state: ExtractionState) -> List[Send]:
    """
    Conditional edge function that evaluates ArtifactObject.source_type 
    and uses LangGraph's Send API to dynamically fan out to specialized extractors.
    """
    sends = []
    for art in state.get("artifacts", []):
        if art.source_type in (SourceType.GIT_DIFF, SourceType.COMMIT, SourceType.FILE_CHANGE):
            sends.append(Send("code_extract", {"artifact": art}))
        elif art.source_type == SourceType.CHAT:
            sends.append(Send("chat_extractor", {"artifact": art}))
        elif art.source_type == SourceType.ERROR_LOG:
            sends.append(Send("error_extractor", {"artifact": art}))
            
    return sends
