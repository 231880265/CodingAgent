from __future__ import annotations

import json

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
