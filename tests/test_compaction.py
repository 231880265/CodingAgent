"""Single-Run context compaction must stay bounded and evidence-safe."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from hako.compaction import ContextBudgetGuard, RunCompactionFacts
from hako.events import VerificationRequired
from hako.history import Conversation, PLACEHOLDER
from hako.loop import Agent, StopReason
from hako.tools import ToolResult
from tests.fake_llm import FakeClient, call, reply


def _guard(
    *,
    enabled: bool = True,
    context_limit: int = 1_000,
    threshold: float = 0.20,
    keep_recent_messages: int = 2,
    enhancer=None,
) -> ContextBudgetGuard:
    return ContextBudgetGuard(
        enabled=enabled,
        context_limit=context_limit,
        threshold=threshold,
        keep_recent_messages=keep_recent_messages,
        run_start_index=0,
        enhancer=enhancer,
    )


def _marker(messages: list[dict]) -> str:
    return next(
        str(message.get("content") or "")
        for message in messages
        if "[COMPACTED RUN STATE]" in str(message.get("content") or "")
    )


def test_below_threshold_keeps_exact_model_view() -> None:
    conversation = Conversation("system rules")
    conversation.add_user("original goal")
    conversation.add_assistant("short answer", [])
    guard = _guard(context_limit=100_000, threshold=0.70)

    prepared = guard.prepare(conversation, [], RunCompactionFacts())

    assert prepared.messages == conversation.to_messages()
    assert not prepared.compacted
    assert guard.compaction_count == 0


def test_threshold_compacts_once_and_protects_goal_constraints_and_recent() -> None:
    conversation = Conversation("system rules")
    conversation.add_user("ORIGINAL GOAL: preserve this exact text")
    conversation.add_assistant("old analysis " * 600, [])
    conversation.add_user("CURRENT CONSTRAINT: do not edit tests")
    conversation.add_assistant("recent answer one", [])
    conversation.add_assistant("recent answer two", [])
    guard = _guard()

    first = guard.prepare(conversation, [], RunCompactionFacts())
    second = guard.prepare(conversation, [], RunCompactionFacts())

    assert first.compacted
    assert guard.compaction_count == 1
    assert first.messages[0] == {"role": "system", "content": "system rules"}
    assert first.messages[1]["content"] == "ORIGINAL GOAL: preserve this exact text"
    assert any(
        message.get("content") == "CURRENT CONSTRAINT: do not edit tests"
        for message in first.messages
    )
    assert [message.get("content") for message in first.messages[-2:]] == [
        "recent answer one",
        "recent answer two",
    ]
    assert "old analysis " * 600 not in str(first.messages)
    assert "[COMPACTED RUN STATE]" in _marker(first.messages)
    assert second.compacted
    assert guard.compaction_count == 1


def test_recent_tool_call_and_result_remain_a_valid_pair() -> None:
    conversation = Conversation("system")
    conversation.add_user("goal")
    conversation.add_assistant("old reasoning " * 500, [])
    read = call("read_file", {"path": "a.py"}, call_id="read-1")
    conversation.add_assistant("inspect current file", [read])
    conversation.add_tool_result(
        "read-1", "read_file", "x = 1", "a.py", ok=True, summary="read a.py"
    )

    prepared = _guard().prepare(conversation, [], RunCompactionFacts())

    assistant = prepared.messages[-2]
    tool = prepared.messages[-1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "read-1"
    assert tool == {"role": "tool", "tool_call_id": "read-1", "content": "x = 1"}


def test_stale_read_source_never_reappears_in_compacted_state() -> None:
    conversation = Conversation("system")
    conversation.add_user("goal")
    read = call("read_file", {"path": "a.py"}, call_id="read-stale")
    conversation.add_assistant("read before edit", [read])
    conversation.add_tool_result(
        "read-stale",
        "read_file",
        "SECRET_OLD_SOURCE = 1",
        "a.py",
        ok=True,
        summary="read a.py",
    )
    assert conversation.invalidate_reads(("a.py",)) == 1
    conversation.add_assistant("analysis after the write " * 400, [])
    conversation.add_assistant("recent one", [])
    conversation.add_assistant("recent two", [])

    prepared = _guard().prepare(conversation, [], RunCompactionFacts())
    rendered = str(prepared.messages)

    assert prepared.compacted
    assert "SECRET_OLD_SOURCE" not in rendered
    assert PLACEHOLDER not in _marker(prepared.messages)
    assert "stale" in _marker(prepared.messages).lower()
    assert conversation.turns[2].stale
    assert conversation.turns[2].message["content"] == PLACEHOLDER


class _FailingEnhancer:
    def enhance(self, summary, trace):
        raise TimeoutError("summary timeout")


def test_enhancer_timeout_falls_back_to_exact_context_without_failing_run(
    caplog,
) -> None:
    conversation = Conversation("system")
    conversation.add_user("goal")
    conversation.add_assistant("large old analysis " * 400, [])
    conversation.add_assistant("recent one", [])
    conversation.add_assistant("recent two", [])
    guard = _guard(
        context_limit=10_000,
        threshold=0.05,
        enhancer=_FailingEnhancer(),
    )

    with caplog.at_level(logging.WARNING, logger="hako.compaction"):
        prepared = guard.prepare(conversation, [], RunCompactionFacts())

    assert prepared.messages == conversation.to_messages()
    assert not prepared.compacted
    assert guard.compaction_count == 0
    assert "compaction_failed" in caplog.text


def test_disabled_compaction_is_byte_for_byte_view_compatible() -> None:
    conversation = Conversation("system")
    conversation.add_user("goal")
    conversation.add_assistant("large content " * 1_000, [])
    expected = conversation.to_messages()

    prepared = _guard(enabled=False).prepare(
        conversation, [], RunCompactionFacts()
    )

    assert prepared.messages == expected
    assert not prepared.compacted


def test_provider_prompt_usage_is_preferred_for_next_budget_decision() -> None:
    conversation = Conversation("system")
    conversation.add_user("goal")
    conversation.add_assistant("old observation", [])
    guard = _guard(context_limit=1_000, threshold=0.50)
    first = guard.prepare(conversation, [], RunCompactionFacts())
    assert not first.compacted
    guard.observe(prompt_tokens=800, estimated_tokens=first.estimated_tokens)
    conversation.add_assistant("recent one", [])
    conversation.add_assistant("recent two", [])

    prepared = guard.prepare(conversation, [], RunCompactionFacts())

    assert prepared.compacted
    assert guard.compaction_count == 1


def test_repository_memory_context_survives_run_compaction() -> None:
    conversation = Conversation(
        "system", memory_context="Repository fact: previous migration used command X"
    )
    conversation.add_user("goal")
    conversation.add_assistant("old analysis " * 600, [])
    conversation.add_assistant("recent one", [])
    conversation.add_assistant("recent two", [])

    prepared = _guard().prepare(conversation, [], RunCompactionFacts())

    assert prepared.compacted
    assert "Repository fact: previous migration used command X" in str(
        prepared.messages[0]["content"]
    )


def test_edit_test_edit_compaction_cannot_bypass_verified_finish(
    config, registry, bus, workspace: Path
) -> None:
    verification_events: list[VerificationRequired] = []
    bus.subscribe(
        lambda event: verification_events.append(event)
        if isinstance(event, VerificationRequired)
        else None
    )
    verifier = registry.get("run_command")
    assert verifier is not None
    outcomes = iter((True, True))

    def run_check(command: str, timeout: int = 60) -> ToolResult:
        ok = next(outcomes)
        return ToolResult(
            ok=ok,
            detail="exit=0\n2 passed",
            summary=f"{command} exit=0",
            verification_kind="test",
            verification_command=command,
        )

    verifier.handler = run_check
    enabled = replace(
        config,
        context_limit=4_000,
        max_steps=8,
        compaction_enabled=True,
        compaction_threshold=0.50,
        compaction_keep_recent_messages=2,
    )
    client = FakeClient(
        [
            reply(
                "write v1",
                [call("write_file", {"path": "a.py", "content": "v1\n"})],
                prompt_tokens=3_000,
            ),
            reply(
                "test v1",
                [call("run_command", {"command": "pytest -q"})],
                prompt_tokens=3_000,
            ),
            reply(
                "write v2",
                [call("write_file", {"path": "a.py", "content": "v2\n"})],
                prompt_tokens=3_000,
            ),
            reply("done", prompt_tokens=3_000),
            reply(
                "test v2",
                [call("run_command", {"command": "pytest -q"})],
                prompt_tokens=3_000,
            ),
            reply("done verified", prompt_tokens=3_000),
        ]
    )
    agent = Agent(
        enabled,
        registry,
        client,
        bus,
        approve=lambda tool, args: True,
    )

    result = agent.run("t")

    assert result.reason is StopReason.DONE_VERIFIED
    assert result.ok
    assert (workspace / "a.py").read_text(encoding="utf-8") == "v2\n"
    assert len(verification_events) == 1
    assert result.verification[-1].command == "pytest -q"
    assert agent.last_context_budget_guard.compaction_count >= 1
    assert any("[COMPACTED RUN STATE]" in str(messages) for messages in client.seen)
