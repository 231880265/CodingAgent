"""Explainable three-factor retrieval with a dense backup channel."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timezone

from .models import EngineeringMemory, RetrievalCandidate


class ThreeFactorRetriever:
    def __init__(
        self,
        *,
        relevance_weight: float = 0.7,
        importance_weight: float = 0.2,
        recency_weight: float = 0.1,
        recency_lambda: float = 0.01,
    ) -> None:
        total = relevance_weight + importance_weight + recency_weight
        if total <= 0:
            raise ValueError("retrieval weights must have a positive sum")
        self.relevance_weight = relevance_weight / total
        self.importance_weight = importance_weight / total
        self.recency_weight = recency_weight / total
        self.recency_lambda = max(0.0, float(recency_lambda))

    def retrieve(
        self,
        query_embedding: list[float],
        memories: list[EngineeringMemory],
        *,
        top_k: int,
        dense_backup_k: int,
        now: datetime | None = None,
    ) -> tuple[list[RetrievalCandidate], tuple[str, ...]]:
        current = now or datetime.now(timezone.utc)
        ranked: list[RetrievalCandidate] = []
        for memory in memories:
            if not memory.active or memory.embedding is None:
                continue
            relevance = max(0.0, min(1.0, (_cosine(query_embedding, memory.embedding) + 1) / 2))
            importance = max(0.0, min(1.0, memory.importance))
            recency = _recency(memory.created_at, current, self.recency_lambda)
            final = (
                self.relevance_weight * relevance
                + self.importance_weight * importance
                + self.recency_weight * recency
            )
            ranked.append(
                RetrievalCandidate(
                    memory=memory,
                    relevance_score=relevance,
                    importance_score=importance,
                    recency_score=recency,
                    final_score=final,
                    recalled_by=("three_factor",),
                )
            )

        primary = sorted(ranked, key=_primary_key, reverse=True)[: max(1, top_k)]
        dense = sorted(ranked, key=_dense_key, reverse=True)[: max(0, dense_backup_k)]
        selected = {candidate.memory.memory_id: candidate for candidate in primary}
        added: list[str] = []
        for candidate in dense:
            memory_id = candidate.memory.memory_id
            if memory_id in selected:
                current_candidate = selected[memory_id]
                selected[memory_id] = replace(
                    current_candidate,
                    recalled_by=tuple(dict.fromkeys((*current_candidate.recalled_by, "dense"))),
                )
                continue
            selected[memory_id] = replace(candidate, recalled_by=("dense",))
            added.append(memory_id)

        combined = sorted(selected.values(), key=_primary_key, reverse=True)
        return combined, tuple(added)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _recency(created_at: datetime | None, now: datetime, decay: float) -> float:
    if created_at is None:
        return 0.5
    created = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (current - created).total_seconds() / 86_400)
    return math.exp(-decay * age_days)


def _primary_key(candidate: RetrievalCandidate) -> tuple[float, float, str]:
    return (
        candidate.final_score,
        candidate.relevance_score,
        candidate.memory.memory_id,
    )


def _dense_key(candidate: RetrievalCandidate) -> tuple[float, float, str]:
    return (
        candidate.relevance_score,
        candidate.final_score,
        candidate.memory.memory_id,
    )
