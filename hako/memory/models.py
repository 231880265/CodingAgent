"""Data contracts for repository engineering memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MemoryType(str, Enum):
    """How a recalled fact may be consumed by the Agent."""

    DURABLE = "DURABLE"
    EVIDENCE = "EVIDENCE"
    STALEABLE = "STALEABLE"


@dataclass
class EngineeringMemory:
    memory_id: str
    repository_id: str
    session_id: str
    source_run_id: str
    content: str
    summary: str | None
    memory_type: MemoryType
    changed_files: list[str] = field(default_factory=list)
    observed_files: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    failed_operations: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    importance: float = 0.35
    embedding: list[float] | None = None
    embedding_source: str | None = None
    created_at: datetime | None = None
    last_accessed_at: datetime | None = None
    active: bool = True
    stale_paths: list[str] = field(default_factory=list)
    is_stale: bool = False


@dataclass(frozen=True)
class RetrievalCandidate:
    memory: EngineeringMemory
    relevance_score: float
    importance_score: float
    recency_score: float
    final_score: float
    recalled_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResponse:
    matches: tuple[RetrievalCandidate, ...]
    dense_fallback_added: tuple[str, ...] = ()
    embedding_fallback_used: bool = False
    rerank_status: str = "disabled"
