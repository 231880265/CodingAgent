"""只读 subagent 的权限、成本统计与单任务调用上限。"""

from __future__ import annotations

from pathlib import Path

from hako.events import EventBus, SubagentFinished
from hako.loop import Agent, StopReason
from hako.prompt import build_system_prompt
from hako.subagent import make_delegate_readonly
from hako.tools import Registry, build_default_registry
from tests.fake_llm import FakeClient, call, reply


class CapturingClient(FakeClient):
    def __init__(self, script):
        super().__init__(script)
        self.tool_names: list[list[str]] = []

    def complete(self, messages, tools):
        self.tool_names.append([item["function"]["name"] for item in tools])
        return super().complete(messages, tools)


def _child_client() -> CapturingClient:
    return CapturingClient(
        [
            reply(
                "",
                [call("list_dir", {"path": "."})],
                prompt_tokens=500,
                completion_tokens=30,
            ),
            reply(
                "",
                [call("read_file", {"path": "a.py"})],
                prompt_tokens=700,
                completion_tokens=20,
            ),
            reply(
                "观察：a.py 第 1 行定义 value。推断：调用方依赖该值。建议主 Agent 核对引用。",
                prompt_tokens=300,
                completion_tokens=40,
            ),
        ]
    )


def test_delegate_is_physically_read_only(config, workspace: Path):
    target = workspace / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    before = target.read_bytes()
    bus = EventBus()
    finished: list[SubagentFinished] = []
    bus.subscribe(
        lambda event: finished.append(event)
        if isinstance(event, SubagentFinished)
        else None
    )
    child = _child_client()
    tool = make_delegate_readonly(config, bus, client_factory=lambda: child)

    result = Registry([tool]).invoke(
        "delegate_readonly", {"task": "只读调查 a.py 的职责与调用证据"}
    )

    assert result.ok
    assert target.read_bytes() == before
    assert tool.read_only is True
    assert tool.needs_approval is False
    assert tool.max_calls_per_run == 1
    assert result.prompt_tokens == 1500
    assert result.completion_tokens == 90
    assert finished and finished[0].reason == "done_read_only"
    assert all(names == ["read_file", "list_dir"] for names in child.tool_names)


def test_parent_counts_child_tokens_and_enforces_one_delegation(
    config, workspace: Path
):
    (workspace / "a.py").write_text("value = 1\n", encoding="utf-8")
    bus = EventBus()
    child = _child_client()
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return child

    delegate = make_delegate_readonly(config, bus, client_factory=factory)
    registry = build_default_registry(workspace, extra_tools=[delegate])
    parent = FakeClient(
        [
            reply(
                "",
                [
                    call(
                        "delegate_readonly",
                        {"task": "调查 a.py"},
                        call_id="d1",
                    ),
                    call(
                        "delegate_readonly",
                        {"task": "再调查一次"},
                        call_id="d2",
                    ),
                ],
                prompt_tokens=100,
                completion_tokens=20,
            ),
            reply("根据已有调查继续", prompt_tokens=200, completion_tokens=30),
        ]
    )
    agent = Agent(config, registry, parent, bus)

    result = agent.run("只读分析")

    assert result.reason is StopReason.DONE_READ_ONLY
    assert result.total_tokens == 100 + 20 + 200 + 30 + 1500 + 90
    assert factory_calls == 1
    assert any(
        "每个任务最多调用 1 次" in str(message.get("content"))
        for message in parent.seen[-1]
        if message["role"] == "tool"
    )


def test_delegation_guidance_only_appears_when_tool_is_enabled(workspace: Path):
    baseline = build_system_prompt(workspace, ["read_file", "edit_file"])
    enabled = build_system_prompt(
        workspace, ["read_file", "edit_file", "delegate_readonly"]
    )

    assert "## 只读委派" not in baseline
    assert "## 只读委派" in enabled
