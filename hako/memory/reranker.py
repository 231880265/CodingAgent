"""Optional lightweight LLM reranking; deterministic ranking stays the fallback."""

from __future__ import annotations

import json
import re
from typing import Protocol, runtime_checkable

from hako.llm import LLMClient

from .models import RetrievalCandidate


@runtime_checkable
class MemoryReranker(Protocol):
    def rerank(
        self, query: str, candidates: list[RetrievalCandidate], limit: int
    ) -> list[RetrievalCandidate]: ...


class LLMReranker:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def rerank(
        self, query: str, candidates: list[RetrievalCandidate], limit: int
    ) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        compact = [
            {
                "id": item.memory.memory_id,
                "type": item.memory.memory_type.value,
                "content": item.memory.content[:600],
                "score": round(item.final_score, 6),
            }
            for item in candidates
        ]
        prompt = (
            "Rank repository engineering memories for the current query. Return only JSON "
            "with selected_ids in best-first order. Never invent ids.\n"
            f"Query: {query}\nCandidates: {json.dumps(compact, ensure_ascii=False)}"
        )
        reply = self.client.complete([{"role": "user", "content": prompt}], [])
        selected = _selected_ids(reply.text)
        by_id = {item.memory.memory_id: item for item in candidates}
        ranked = [by_id[memory_id] for memory_id in selected if memory_id in by_id]
        if not ranked:
            raise ValueError("reranker returned no valid memory ids")
        return ranked[: max(1, limit)]


def _selected_ids(text: str) -> list[str]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    value = json.loads(cleaned)
    if not isinstance(value, dict) or not isinstance(value.get("selected_ids"), list):
        raise ValueError("reranker response must contain selected_ids")
    return [item for item in value["selected_ids"] if isinstance(item, str)]
