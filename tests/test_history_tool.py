from __future__ import annotations

import json

from hako.memory import MemorySettings, RepositoryMemoryService
from web.worker.history_tool import MemoryIndex, make_search_session_history


def memory(run_id: str, goal: str, *, failed: bool = False) -> dict:
    return {
        "schemaVersion": "1.0",
        "sessionId": "session-a",
        "runId": run_id,
        "status": "FAILED" if failed else "COMPLETED",
        "stopReason": "error" if failed else "done_verified",
        "userGoal": goal,
        "changes": {"created": [], "modified": ["app/exporter.py"], "deleted": [], "derived": []},
        "verifications": [
            {
                "kind": "test",
                "executedCommand": "python -m pytest tests/test_csv.py -q",
                "status": "failed" if failed else "succeeded",
                "exitCode": 1 if failed else 0,
                "ok": not failed,
                "summary": "CSV regression failed" if failed else "1 passed",
            }
        ],
        "approvals": [],
        "toolFailures": [{"summary": "CSV regression failed"}] if failed else [],
        "semanticSummary": {"text": "model prose", "authoritative": False},
        "evidenceIds": [{"eventId": 7, "type": "tool_call_finished"}],
    }


def test_history_search_returns_exact_event_facts() -> None:
    index = MemoryIndex(
        [memory("run-1", "修复 CSV 导出", failed=True), memory("run-2", "重新验证 CSV 导出")]
    )
    tool = make_search_session_history(index)

    result = tool.handler(query="之前 CSV 验证失败")
    payload = json.loads(result.detail)

    assert result.ok
    assert payload["searchedScopes"] == ["session"]
    assert payload["selectedScope"] == "session"
    assert payload["matches"][0]["runId"] == "run-1"
    assert payload["matches"][0]["verifications"][0]["exitCode"] == 1
    assert payload["matches"][0]["semanticSummary"]["authoritative"] is False


def test_history_search_is_bounded_and_can_filter_run() -> None:
    index = MemoryIndex([memory(f"run-{i}", f"CSV goal {i}") for i in range(20)])
    matches = index.search("CSV", limit=99)
    assert len(matches) == 8
    assert index.search("CSV", run_id="run-3")[0]["runId"] == "run-3"


def test_session_context_warns_that_workspace_is_authoritative() -> None:
    index = MemoryIndex([memory("run-1", "修复 CSV 导出")])
    context = index.session_context()
    assert "run=run-1" in context
    assert "app/exporter.py" in context


def test_empty_session_search_falls_back_to_same_workspace_history() -> None:
    prior = memory("run-prior", "fix published campaign revision")
    prior["sessionId"] = "session-prior"
    prior["changes"]["modified"] = ["app/repositories/campaign_repository.py"]
    repository = RepositoryMemoryService(
        "D:/same-workspace",
        [prior],
        settings=MemorySettings(top_k=4, dense_backup_k=2, rerank_top_k=4),
    )
    tool = make_search_session_history(
        MemoryIndex([]), repository_memory=repository
    )

    result = tool.handler(query="published campaign revision")
    payload = json.loads(result.detail)

    assert result.ok
    assert payload["searchedScopes"] == ["session", "repository"]
    assert payload["selectedScope"] == "repository"
    assert payload["sessionMatchCount"] == 0
    assert payload["matches"][0]["sourceRunId"] == "run-prior"
    assert "campaign_repository.py" in payload["matches"][0]["content"]
    assert result.summary.startswith("history fallback: repository")


class _CountingRepositoryMemory(RepositoryMemoryService):
    def __init__(self) -> None:
        super().__init__("D:/same-workspace", [memory("run-prior", "CSV prior")])
        self.search_calls = 0

    def search(self, query: str):
        self.search_calls += 1
        return super().search(query)


def test_current_session_match_wins_without_repository_search() -> None:
    repository = _CountingRepositoryMemory()
    tool = make_search_session_history(
        MemoryIndex([memory("run-current", "CSV current")]),
        repository_memory=repository,
    )

    result = tool.handler(query="CSV")
    payload = json.loads(result.detail)

    assert payload["selectedScope"] == "session"
    assert payload["searchedScopes"] == ["session"]
    assert payload["matches"][0]["runId"] == "run-current"
    assert repository.search_calls == 0
