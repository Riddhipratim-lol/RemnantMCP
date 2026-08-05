"""
Layer 4 — Phase 4: Context Packer & Token Budgeting

Iterates through ranked (MemoryObject, score) tuples and formats each
memory into a human-readable block.  It uses tiktoken to count tokens
and stops inserting memories once the configurable token budget is
exhausted.

Output format mirrors the canonical example in Project_Vision.md §8:

    === PROJECT MEMORY CONTEXT ===

    [ARCHITECTURAL DECISION -- Auth Service -- 2025-06-12]
    Decision: JWT-based stateless authentication …
    Rationale: …
    Related Files: src/auth/jwt_handler.py, src/middleware/auth.py

    ...

    === END MEMORY CONTEXT ===
"""

from datetime import datetime
from typing import List, Optional, Tuple

import tiktoken

from remnant.structures import MemoryObject, MemoryType


# Use cl100k_base as the reference tokenizer (GPT-4 / text-embedding-3 family)
_TOKENIZER_ENCODING = "cl100k_base"

# Human-readable labels for MemoryType values
_TYPE_LABELS: dict = {
    MemoryType.ARCHITECTURAL_DECISION: "ARCHITECTURAL DECISION",
    MemoryType.IMPLEMENTATION_RATIONALE: "IMPLEMENTATION RATIONALE",
    MemoryType.FAILED_APPROACH: "FAILED APPROACH",
    MemoryType.BUG_RESOLUTION: "BUG RESOLUTION",
    MemoryType.DESIGN_TRADEOFF: "DESIGN TRADEOFF",
    MemoryType.COMPONENT_RELATIONSHIP: "COMPONENT RELATIONSHIP",
    MemoryType.CONSTRAINT: "CONSTRAINT",
}


class ContextPacker:
    """
    Assembles a token-budgeted context block from ranked memories.

    Args:
        max_tokens: Upper token limit for the final context string.
                    Defaults to 8 000 — sized for Gemini-class AI clients
                    (1M token context window) where richer context directly
                    improves code suggestion quality.  Set lower if targeting
                    a client with a tighter context budget.
    """

    HEADER = "=== PROJECT MEMORY CONTEXT ==="
    FOOTER = "=== END MEMORY CONTEXT ==="

    def __init__(self, max_tokens: int = 8_000):
        self.max_tokens = max_tokens
        try:
            self._enc = tiktoken.get_encoding(_TOKENIZER_ENCODING)
        except Exception:
            self._enc = None  # graceful degradation: fall back to char-based estimate

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pack(
        self,
        ranked_memories: List[Tuple[MemoryObject, float]],
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Build the formatted context block, respecting the token budget.

        Args:
            ranked_memories: Sorted list of (MemoryObject, score) from Phase 3.
            max_tokens:      Override the instance-level budget for this call.

        Returns:
            A multi-line string ready to prepend to an AI assistant's prompt.
        """
        budget = max_tokens if max_tokens is not None else self.max_tokens

        # Reserve tokens for the header/footer framing
        framing_tokens = self._count_tokens(f"{self.HEADER}\n\n{self.FOOTER}")
        remaining_budget = budget - framing_tokens

        selected_blocks: List[str] = []

        for mem, _score in ranked_memories:
            block = self._format_memory(mem)
            block_tokens = self._count_tokens(block)

            if block_tokens > remaining_budget:
                break  # Token budget exhausted

            selected_blocks.append(block)
            remaining_budget -= block_tokens

        if not selected_blocks:
            return f"{self.HEADER}\n\n(No relevant memories found within token budget.)\n\n{self.FOOTER}"

        body = "\n\n".join(selected_blocks)
        return f"{self.HEADER}\n\n{body}\n\n{self.FOOTER}"

    def count_tokens(self, text: str) -> int:
        """Public wrapper for external callers that need a token count."""
        return self._count_tokens(text)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _count_tokens(self, text: str) -> int:
        """
        Return the token count using tiktoken.  Falls back to a word-based
        approximation if tiktoken is unavailable.
        """
        if self._enc is not None:
            return len(self._enc.encode(text))
        # Rough heuristic: 1 token ≈ 0.75 words
        return int(len(text.split()) / 0.75)

    @staticmethod
    def _format_date(dt: Optional[datetime]) -> str:
        if dt is None:
            return "Unknown date"
        return dt.strftime("%Y-%m-%d")

    def _format_memory(self, mem: MemoryObject) -> str:
        """
        Render a single MemoryObject into the canonical display format.
        """
        type_label = _TYPE_LABELS.get(mem.memory_type, str(mem.memory_type))
        component = mem.components[0] if mem.components else "Global"
        date_str = self._format_date(mem.created_at)

        # Header line
        lines = [f"[{type_label} -- {component} -- {date_str}]"]

        # Memory-type-specific field labels
        if mem.memory_type == MemoryType.FAILED_APPROACH:
            lines.append(f"Attempted: {mem.title}")
            if mem.content:
                lines.append(f"Details: {mem.content}")
            if mem.rationale:
                lines.append(f"Abandoned Because: {mem.rationale}")
        elif mem.memory_type == MemoryType.CONSTRAINT:
            lines.append(f"Constraint: {mem.title}")
            if mem.content:
                lines.append(f"Impact: {mem.content}")
        elif mem.memory_type == MemoryType.BUG_RESOLUTION:
            lines.append(f"Bug: {mem.title}")
            if mem.content:
                lines.append(f"Root Cause: {mem.content}")
            if mem.rationale:
                lines.append(f"Fix Rationale: {mem.rationale}")
        else:
            lines.append(f"Decision: {mem.title}")
            if mem.content:
                lines.append(f"Details: {mem.content}")
            if mem.rationale:
                lines.append(f"Rationale: {mem.rationale}")

        if mem.file_paths:
            lines.append(f"Related Files: {', '.join(mem.file_paths)}")

        if mem.tags:
            lines.append(f"Tags: {', '.join(mem.tags)}")

        return "\n".join(lines)
