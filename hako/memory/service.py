"""Repository memory lifecycle, retrieval and graceful provider fallbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hako import events as ev

from .embedding import EmbeddingProvider, HashingEmbeddingProvider
from .models import EngineeringMemory, MemoryType, SearchResponse
from .reranker import MemoryReranker
from .retriever import ThreeFactorRetriever
from .writer import EngineeringMemoryWriter, repository_id


@dataclass(frozen=True)
class MemorySettings:
    top_k: int = 6
    dense_backup_k: int = 4
    rerank_top_k: int = 4
    relevance_weight: float = 0.7
    importance_weight: float = 0.2
    recency_weight: float = 0.1
    recency_lambda: float = 0.01


class RepositoryMemoryService:
    def __init__(
        self,
        workspace: str,
        run_memories: list[dict[str, Any]] | None = None,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        fallback_provider: EmbeddingProvider | None = None,
        reranker: MemoryReranker | None = None,
        settings: MemorySettings | None = None,
    ) -> None:
        self.settings = settings or MemorySettings()
        self.embedding_provider = embedding_provider or HashingEmbeddingProvider()
        self.fallback_provider = fallback_provider or HashingEmbeddingProvider()
        self._embedding_source = _provider_key(self.embedding_provider)
        self._fallback_source = _provider_key(self.fallback_provider)
        self.reranker = reranker
        self.writer = EngineeringMemoryWriter(repository_id(workspace))
        self.retriever = ThreeFactorRetriever(
            relevance_weight=self.settings.relevance_weight,
            importance_weight=self.settings.importance_weight,
            recency_weight=self.settings.recency_weight,
            recency_lambda=self.settings.recency_lambda,
        )
        self.memories: list[EngineeringMemory] = []
        self.update(run_memories or [])

    def update(self, run_memories: list[dict[str, Any]]) -> None:
        previous = {memory.memory_id: memory for memory in self.memories}
        rebuilt = self.writer.build(run_memories)
        for memory in rebuilt:
            old = previous.get(memory.memory_id)
            if old is not None and old.embedding is not None:
                memory.embedding = old.embedding
                memory.embedding_source = old.embedding_source
        self.memories = rebuilt

    def search(self, query: str) -> SearchResponse:
        query = query.strip()
        if not query:
            raise ValueError("query cannot be empty")
        usable = [memory for memory in self.memories if memory.active]
        if not usable:
            return SearchResponse(matches=())
        fallback_used = False
        try:
            missing = [
                memory
                for memory in usable
                if memory.embedding is None
                or memory.embedding_source != self._embedding_source
            ]
            if missing:
                vectors = self.embedding_provider.embed([memory.content for memory in missing])
                if len(vectors) != len(missing):
                    raise ValueError("embedding provider returned a mismatched vector count")
                for memory, vector in zip(missing, vectors, strict=True):
                    memory.embedding = vector
                    memory.embedding_source = self._embedding_source
            query_vector = self.embedding_provider.embed([query])[0]
        except Exception:  # noqa: BLE001 - optional providers must have a local fallback
            fallback_used = True
            missing = [
                memory
                for memory in usable
                if memory.embedding is None
                or memory.embedding_source != self._fallback_source
            ]
            if missing:
                vectors = self.fallback_provider.embed([memory.content for memory in missing])
                if len(vectors) != len(missing):
                    raise ValueError("fallback provider returned a mismatched vector count")
                for memory, vector in zip(missing, vectors, strict=True):
                    memory.embedding = vector
                    memory.embedding_source = self._fallback_source
            query_vector = self.fallback_provider.embed([query])[0]

        ranked, dense_added = self.retriever.retrieve(
            query_vector,
            usable,
            top_k=self.settings.top_k,
            dense_backup_k=self.settings.dense_backup_k,
        )
        rerank_status = "disabled"
        if self.reranker is not None and ranked:
            try:
                ranked = self.reranker.rerank(query, ranked, self.settings.rerank_top_k)
                rerank_status = "applied"
            except Exception:  # noqa: BLE001 - deterministic ranking remains authoritative fallback
                rerank_status = "fallback"
        else:
            ranked = ranked[: self.settings.rerank_top_k]
        for candidate in ranked:
            candidate.memory.last_accessed_at = _utcnow()
        return SearchResponse(
            matches=tuple(ranked),
            dense_fallback_added=dense_added,
            embedding_fallback_used=fallback_used,
            rerank_status=rerank_status,
        )

    def observe_event(self, event: ev.Event) -> None:
        if not isinstance(event, ev.ToolCallFinished) or not event.ok:
            return
        paths = event.touched_paths
        if paths:
            self.invalidate_paths(paths)

    def invalidate_paths(self, paths: tuple[str, ...] | list[str]) -> None:
        changed = {_path(path) for path in paths}
        for memory in self.memories:
            if memory.memory_type is not MemoryType.STALEABLE:
                continue
            stale = sorted(changed & {_path(path) for path in memory.observed_files})
            if stale:
                memory.is_stale = True
                memory.stale_paths = sorted(set(memory.stale_paths) | set(stale))


def _path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _provider_key(provider: EmbeddingProvider) -> str:
    details = getattr(provider, "model_name", None)
    if details is None:
        details = getattr(provider, "dimensions", None)
    suffix = "" if details is None else f":{details}"
    return f"{type(provider).__module__}.{type(provider).__qualname__}{suffix}"
