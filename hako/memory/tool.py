"""Read-only tool exposing repository engineering experience to the model."""

from __future__ import annotations

import json
from typing import Any

from hako.tools import Tool, ToolResult

from .policy import render_for_agent
from .service import RepositoryMemoryService


def repository_memory_payload(
    service: RepositoryMemoryService, query: str
) -> dict[str, Any]:
    """Return the shared, structured repository-memory search payload."""
    response = service.search(query)
    return {
        "notice": (
            "This is repository experience, not current source truth. "
            "Re-read current files before editing."
        ),
        "weights": {
            "relevance": service.retriever.relevance_weight,
            "importance": service.retriever.importance_weight,
            "recency": service.retriever.recency_weight,
        },
        "embeddingFallbackUsed": response.embedding_fallback_used,
        "denseFallbackAdded": list(response.dense_fallback_added),
        "rerankStatus": response.rerank_status,
        "matches": [
            {
                "memoryId": item.memory.memory_id,
                "sourceRunId": item.memory.source_run_id,
                "type": item.memory.memory_type.value,
                "content": render_for_agent(item.memory),
                "scores": {
                    "relevance": round(item.relevance_score, 6),
                    "importance": round(item.importance_score, 6),
                    "recency": round(item.recency_score, 6),
                    "final": round(item.final_score, 6),
                },
                "recalledBy": list(item.recalled_by),
            }
            for item in response.matches
        ],
    }


def make_search_repository_memory(
    service: RepositoryMemoryService, budget: int = 6_000
) -> Tool:
    def handler(query: str) -> ToolResult:
        payload = repository_memory_payload(service, query)
        detail = _clip(json.dumps(payload, ensure_ascii=False, indent=2), budget)
        return ToolResult(
            ok=True,
            detail=detail,
            summary=f"repository memory: {len(payload['matches'])} match(es)",
        )

    return Tool(
        name="search_repository_memory",
        description=(
            "Search reusable engineering experience from earlier Sessions in this repository. "
            "Use only when a task may benefit from a prior fix, failure, constraint or verification "
            "pattern. Historical code observations may be stale, so re-read current files before editing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The engineering problem, constraint, failure or verification to recall",
                }
            },
            "required": ["query"],
        },
        handler=handler,
        read_only=True,
        max_calls_per_run=4,
    )


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 36)] + "\n[repository memory truncated]"
