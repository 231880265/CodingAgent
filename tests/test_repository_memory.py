from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hako import events as ev
from hako.memory import (
    EngineeringMemory,
    EngineeringMemoryWriter,
    HashingEmbeddingProvider,
    MemorySettings,
    MemoryType,
    RepositoryMemoryService,
    make_search_repository_memory,
)
from hako.memory.retriever import ThreeFactorRetriever


def _run_memory(
    run_id: str,
    *,
    goal: str = "fix checkout",
    changed: list[str] | None = None,
    observed: list[str] | None = None,
    constraints: list[str] | None = None,
    finished_at: str = "2026-08-31T10:00:00Z",
) -> dict:
    return {
        "sessionId": "session-1",
        "runId": run_id,
        "userGoal": goal,
        "finishedAt": finished_at,
        "changes": {
            "created": [],
            "modified": changed or [],
            "deleted": [],
            "derived": [],
        },
        "observedFiles": observed or [],
        "decisions": [],
        "constraints": constraints or [],
        "toolFailures": [],
        "verifications": [
            {
                "executedCommand": "python -m pytest -q",
                "status": "succeeded",
                "ok": True,
                "exitCode": 0,
            }
        ],
        "semanticSummary": {"text": "checkout regression fixed"},
    }


def test_writer_keeps_hard_facts_and_invalidates_old_observations() -> None:
    writer = EngineeringMemoryWriter("repo-test")

    memories = writer.build(
        [
            _run_memory(
                "run-1",
                observed=["app/pricing.py"],
                constraints=["keep the public API compatible"],
            ),
            _run_memory(
                "run-2",
                changed=["./app\\pricing.py"],
                finished_at="2026-08-31T11:00:00Z",
            ),
        ]
    )

    observation = next(
        item
        for item in memories
        if item.source_run_id == "run-1" and item.memory_type is MemoryType.STALEABLE
    )
    durable = next(
        item
        for item in memories
        if item.source_run_id == "run-1" and item.memory_type is MemoryType.DURABLE
    )
    later_evidence = next(
        item
        for item in memories
        if item.source_run_id == "run-2" and item.memory_type is MemoryType.EVIDENCE
    )

    assert observation.is_stale is True
    assert observation.stale_paths == ["app/pricing.py"]
    assert durable.constraints == ["keep the public API compatible"]
    assert later_evidence.changed_files == ["app/pricing.py"]
    assert later_evidence.verification_commands == [
        "python -m pytest -q -> succeeded (exitCode=0)"
    ]


def test_three_factor_ranking_retains_score_components_and_dense_backup() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    exact = EngineeringMemory(
        memory_id="exact",
        repository_id="repo",
        session_id="s1",
        source_run_id="r1",
        content="exact",
        summary=None,
        memory_type=MemoryType.EVIDENCE,
        importance=0.5,
        embedding=[1.0, 0.0],
        created_at=now,
    )
    backup = EngineeringMemory(
        memory_id="backup",
        repository_id="repo",
        session_id="s2",
        source_run_id="r2",
        content="backup",
        summary=None,
        memory_type=MemoryType.EVIDENCE,
        importance=0.1,
        embedding=[0.0, 1.0],
        created_at=now - timedelta(days=10),
    )
    retriever = ThreeFactorRetriever(
        relevance_weight=0.7,
        importance_weight=0.2,
        recency_weight=0.1,
        recency_lambda=0.01,
    )

    ranked, dense_added = retriever.retrieve(
        [1.0, 0.0], [exact, backup], top_k=1, dense_backup_k=2, now=now
    )

    assert ranked[0].memory.memory_id == "exact"
    assert ranked[0].relevance_score == pytest.approx(1.0)
    assert ranked[0].importance_score == pytest.approx(0.5)
    assert ranked[0].recency_score == pytest.approx(1.0)
    assert ranked[0].final_score == pytest.approx(0.9)
    assert dense_added == ("backup",)
    backup_result = next(item for item in ranked if item.memory.memory_id == "backup")
    assert backup_result.recalled_by == ("dense",)


class _BrokenEmbeddingProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("encoder unavailable")


class _BrokenReranker:
    def rerank(self, query, candidates, limit):
        raise RuntimeError("reranker unavailable")


class _RecoveringEmbeddingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("encoder temporarily unavailable")
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_optional_embedding_and_reranker_have_deterministic_fallbacks() -> None:
    service = RepositoryMemoryService(
        "D:/repo",
        [_run_memory("run-1", goal="fix published campaign revision")],
        embedding_provider=_BrokenEmbeddingProvider(),
        fallback_provider=HashingEmbeddingProvider(dimensions=64),
        reranker=_BrokenReranker(),
        settings=MemorySettings(top_k=3, dense_backup_k=1, rerank_top_k=2),
    )

    response = service.search("published campaign revision")

    assert response.matches
    assert response.embedding_fallback_used is True
    assert response.rerank_status == "fallback"


def test_recovered_provider_reembeds_memories_after_a_fallback() -> None:
    provider = _RecoveringEmbeddingProvider()
    service = RepositoryMemoryService(
        "D:/repo",
        [_run_memory("run-1", goal="fix published campaign revision")],
        embedding_provider=provider,
        fallback_provider=HashingEmbeddingProvider(dimensions=64),
    )

    first = service.search("published revision")
    second = service.search("published revision")

    assert first.embedding_fallback_used is True
    assert second.embedding_fallback_used is False
    assert all(len(match.memory.embedding or []) == 3 for match in second.matches)


def test_repository_memory_tool_is_read_only_and_marks_stale_evidence() -> None:
    service = RepositoryMemoryService(
        "D:/repo",
        [
            _run_memory("run-1", observed=["app/pricing.py"]),
            _run_memory(
                "run-2",
                changed=["app/pricing.py"],
                finished_at="2026-08-31T11:00:00Z",
            ),
        ],
        settings=MemorySettings(top_k=8, dense_backup_k=8, rerank_top_k=8),
    )
    tool = make_search_repository_memory(service)

    result = tool.handler(query="pricing.py")

    assert tool.read_only is True
    assert tool.needs_approval is False
    assert tool.max_calls_per_run == 4
    assert result.ok is True
    assert "STALE HISTORICAL OBSERVATION" in result.detail
    assert "Re-read the current workspace" in result.detail
    assert '"relevance"' in result.detail
    assert '"importance"' in result.detail
    assert '"recency"' in result.detail


def test_successful_file_change_event_invalidates_matching_observation() -> None:
    service = RepositoryMemoryService(
        "D:/repo", [_run_memory("run-1", observed=["app/pricing.py"])]
    )

    service.observe_event(
        ev.ToolCallFinished(
            call_id="edit-1",
            name="edit_file",
            ok=True,
            summary="updated",
            detail="updated",
            duration_ms=2,
            touched_paths=("./app\\pricing.py",),
            modified_paths=("app/pricing.py",),
        )
    )

    observation = next(
        item for item in service.memories if item.memory_type is MemoryType.STALEABLE
    )
    assert observation.is_stale is True
    assert observation.stale_paths == ["app/pricing.py"]
