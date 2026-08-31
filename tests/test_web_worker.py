from __future__ import annotations

import io
import json

import pytest

from hako import events as ev
from hako.loop import RunResult, StopReason, VerificationEvidence
from web.worker.main import _semantic_history
from web.worker.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    ProtocolWriter,
    event_payload,
    read_message,
    redact,
    result_payload,
)


@pytest.mark.parametrize(
    ("event", "kind", "expected"),
    [
        (ev.RunStarted(task="修复", model="m", cwd="D:/项目"), "run_started", {"cwd": "D:/项目"}),
        (ev.TurnStarted(step=2, max_steps=40), "turn_started", {"maxSteps": 40}),
        (ev.ContextStats(used_tokens=8, limit=100, message_count=3), "context_stats", {"messageCount": 3}),
        (
            ev.RunFinished(
                reason="done_verified",
                steps=3,
                total_tokens=90,
                changed_paths=("a.py",),
                verification="1 passed",
            ),
            "run_finished",
            {"changedPaths": ["a.py"]},
        ),
    ],
)
def test_event_payload_uses_documented_camel_case(event, kind, expected) -> None:
    actual_kind, payload = event_payload(event)
    assert actual_kind == kind
    assert payload.items() >= expected.items()


def test_all_declared_events_have_explicit_mapping() -> None:
    events = [
        ev.RunStarted(task="t", model="m", cwd="."),
        ev.TurnStarted(step=1, max_steps=2),
        ev.AssistantText(text="x"),
        ev.ToolCallStarted(call_id="c", name="read_file", args={}),
        ev.ToolCallFinished(call_id="c", name="read_file", ok=True, summary="s", detail="d", duration_ms=1),
        ev.ContextStats(used_tokens=1, limit=2, message_count=3),
        ev.VerificationRequired(changed_paths=("a.py",), message="m"),
        ev.ContinuationRequired(attempt=1, max_attempts=2, finish_reason="length", message="m"),
        ev.SubagentStarted(task="t", max_steps=2),
        ev.SubagentFinished(ok=True, reason="done", steps=1, total_tokens=2, max_context_tokens=3),
        ev.RunFinished(reason="done_read_only", steps=1, total_tokens=2),
        ev.AgentError(message="e"),
    ]
    assert {event_payload(item)[0] for item in events} == {item.kind for item in events}


def test_tool_finished_payload_exposes_facts_for_web_presentation() -> None:
    event = ev.ToolCallFinished(
        call_id="compile-1",
        name="run_command",
        ok=True,
        summary="g++ qsort.cpp -o qsort.exe exit=0",
        detail="exit=0",
        duration_ms=120,
        touched_paths=("qsort.exe",),
        created_paths=("qsort.exe",),
        derived_paths=("qsort.exe",),
        verification_kind="build",
        verification_command="g++ qsort.cpp -o qsort.exe",
    )

    kind, payload = event_payload(event)

    assert kind == "tool_call_finished"
    assert payload["derivedPaths"] == ["qsort.exe"]
    assert payload["verificationKind"] == "build"
    assert payload["verificationCommand"] == "g++ qsort.cpp -o qsort.exe"


def test_writer_allocates_contiguous_session_sequence() -> None:
    stream = io.StringIO()
    writer = ProtocolWriter(stream)
    writer.ready(12)
    writer.run_message(
        "event",
        "session-1",
        "run-1",
        {"kind": "assistant_text", "data": {"text": "中"}},
    )
    writer.run_message("result", "session-1", "run-1", {"success": True})
    messages = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert messages[0]["type"] == "ready"
    assert "sequence" not in messages[0]
    assert [message["sequence"] for message in messages[1:]] == [1, 2]
    assert {message["sessionId"] for message in messages[1:]} == {"session-1"}
    assert {message["runId"] for message in messages[1:]} == {"run-1"}
    assert all(message["protocolVersion"] == PROTOCOL_VERSION for message in messages)


def test_result_payload_preserves_verified_finish_evidence() -> None:
    result = RunResult(
        reason=StopReason.DONE_VERIFIED,
        steps=4,
        total_tokens=99,
        final_text="完成",
        changed_paths=("a.py",),
        verification=(VerificationEvidence("test", "python -m pytest", "2 passed", 3),),
    )
    payload = result_payload(result)
    assert payload["success"] is True
    assert payload["stopReason"] == "done_verified"
    assert payload["verification"][0]["step"] == 3


def test_input_rejects_wrong_version_and_redacts_secrets() -> None:
    with pytest.raises(ProtocolError, match="protocolVersion"):
        read_message(io.StringIO('{"protocolVersion":"2","type":"start"}\n'))
    assert "secret-token" not in redact("API_KEY=secret-token")
    fake_key = "sk-" + "abcdefghijklmnop"
    assert fake_key not in redact(f"Bearer {fake_key}")


def test_worker_accepts_only_complete_semantic_conversation_pairs() -> None:
    history = _semantic_history(
        {
            "conversation": [
                {"role": "user", "content": "先定位问题"},
                {"role": "assistant", "content": "已确认根因"},
            ]
        }
    )
    assert history == [
        {"role": "user", "content": "先定位问题"},
        {"role": "assistant", "content": "已确认根因"},
    ]

    with pytest.raises(ProtocolError, match="未回答"):
        _semantic_history({"conversation": [{"role": "user", "content": "悬空输入"}]})
