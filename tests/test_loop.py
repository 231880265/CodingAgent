"""主循环与终止条件。

这里测的是"什么时候停、为什么停"。五种终止原因各自对应一种真实失败模式，
所以每种都单独验一遍，而不是笼统测个"能跑完"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hako.events import EventBus, ToolCallFinished
from hako.loop import STUCK_THRESHOLD, Agent, StopReason
from tests.fake_llm import ExplodingClient, FakeClient, call, reply


def agent_with(script, config, registry, bus, approve=None) -> tuple[Agent, FakeClient]:
    client = FakeClient(script)
    return Agent(config, registry, client, bus, approve=approve), client


# ------------------------------------------------------------------ DONE


def test_no_tool_calls_means_done(config, registry, bus):
    agent, client = agent_with([reply("已完成")], config, registry, bus)
    result = agent.run("任务")

    assert result.reason is StopReason.DONE
    assert result.ok
    assert result.steps == 1
    assert result.final_text == "已完成"


def test_tool_then_finish(config, registry, bus, workspace: Path):
    (workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    agent, client = agent_with(
        [
            reply("先读一下", [call("read_file", {"path": "a.py"})]),
            reply("读到了 x = 1"),
        ],
        config, registry, bus,
    )
    result = agent.run("看看 a.py")

    assert result.reason is StopReason.DONE
    assert result.steps == 2
    # 第二次请求里必须带上工具结果，否则模型等于没读
    assert any("x = 1" in str(m.get("content")) for m in client.seen[1])


def test_usage_accumulates_from_api_not_estimate(config, registry, bus):
    agent, _ = agent_with(
        [
            reply("a", [call("list_dir", {})], prompt_tokens=500, completion_tokens=30),
            reply("done", prompt_tokens=700, completion_tokens=10),
        ],
        config, registry, bus,
    )
    assert agent.run("t").total_tokens == 500 + 30 + 700 + 10


# ------------------------------------------------------------------ MAX_STEPS


def test_max_steps_caps_runaway(config, registry, bus):
    """模型永远在调工具时，步数上限是最后一道闸。"""
    script = [reply("still going", [call("list_dir", {"path": f"d{i}"})]) for i in range(50)]
    agent, _ = agent_with(script, config, registry, bus)
    result = agent.run("永动机")

    assert result.reason is StopReason.MAX_STEPS
    assert result.steps == config.max_steps
    assert not result.ok


# ------------------------------------------------------------------ STUCK


def test_identical_call_repeated_triggers_stuck(config, registry, bus):
    same = lambda: call("read_file", {"path": "missing.py"}, call_id="fixed")
    agent, client = agent_with(
        [reply("retry", [same()]) for _ in range(10)], config, registry, bus
    )
    result = agent.run("读一个不存在的文件")

    assert result.reason is StopReason.STUCK
    # 比步数上限更早生效，否则烧满 40 步既费钱又费时间
    assert result.steps == STUCK_THRESHOLD
    assert result.steps < config.max_steps


def test_nudge_precedes_termination(config, registry, bus, workspace: Path):
    """阶梯式干预：先提醒，给模型一次自我修正的机会，而不是直接判死。"""
    (workspace / "a.py").write_text("x\n", encoding="utf-8")
    same = lambda: call("read_file", {"path": "a.py"}, call_id="fixed")
    agent, client = agent_with(
        [reply("", [same()]) for _ in range(5)], config, registry, bus
    )
    agent.run("t")

    nudged = [
        m for msgs in client.seen for m in msgs
        if m["role"] == "tool" and "[系统提示]" in str(m.get("content"))
    ]
    assert nudged, "第 2 次重复时应当先给提醒"


def test_different_args_are_not_stuck(config, registry, bus, workspace: Path):
    """参数不同就是有进展，不能误判。"""
    for name in ("a", "b", "c", "d"):
        (workspace / f"{name}.py").write_text("x\n", encoding="utf-8")
    agent, _ = agent_with(
        [reply("", [call("read_file", {"path": f"{n}.py"})]) for n in "abcd"]
        + [reply("看完了")],
        config, registry, bus,
    )
    assert agent.run("逐个读").reason is StopReason.DONE


def test_signature_ignores_key_order(config, registry, bus, workspace: Path):
    """{path, limit} 和 {limit, path} 是同一次调用。签名用排序后的 JSON
    就是为了不被键序骗过去。"""
    (workspace / "a.py").write_text("x\n", encoding="utf-8")
    variants = [
        {"path": "a.py", "limit": 5},
        {"limit": 5, "path": "a.py"},
        {"path": "a.py", "limit": 5},
    ]
    agent, _ = agent_with(
        [reply("", [call("read_file", v, call_id=f"c{i}")]) for i, v in enumerate(variants)]
        + [reply("done")],
        config, registry, bus,
    )
    assert agent.run("t").reason is StopReason.STUCK


# ------------------------------------------------------------------ DENIED


def test_user_denial_terminates_without_executing(config, registry, bus, workspace: Path):
    """人的决定不重试。"""
    agent, _ = agent_with(
        [reply("", [call("write_file", {"path": "x.py", "content": "boom"})])],
        config, registry, bus,
        approve=lambda tool, args: False,
    )
    result = agent.run("写个文件")

    assert result.reason is StopReason.DENIED
    assert not (workspace / "x.py").exists()


def test_denial_still_records_a_tool_message(config, registry, bus):
    """拒绝也要回一条 tool 消息，否则历史里 tool_call 悬空，下轮请求被拒。"""
    agent, _ = agent_with(
        [reply("", [call("write_file", {"path": "x.py", "content": "c"}, call_id="w1")])],
        config, registry, bus,
        approve=lambda tool, args: False,
    )
    agent.run("t")
    assert any(
        m["role"] == "tool" and m["tool_call_id"] == "w1"
        for m in agent.conversation.to_messages()
    )


def test_read_only_tool_needs_no_approval(config, registry, bus, workspace: Path):
    """只读工具不问：每次读文件都弹确认，用户会直接关掉这个 agent。"""
    (workspace / "a.py").write_text("x\n", encoding="utf-8")
    denied: list[str] = []

    def approve(tool, args):
        denied.append(tool.name)
        return False

    agent, _ = agent_with(
        [reply("", [call("read_file", {"path": "a.py"})]), reply("done")],
        config, registry, bus, approve=approve,
    )
    assert agent.run("t").reason is StopReason.DONE
    assert denied == []


# ------------------------------------------------------------------ ERROR


def test_api_failure_is_unrecoverable(config, registry, bus):
    agent, _ = agent_with([], config, registry, bus)
    agent.client = ExplodingClient(RuntimeError("401 invalid api key"))
    result = agent.run("t")

    assert result.reason is StopReason.ERROR
    assert not result.ok


def test_error_mid_run_keeps_earlier_progress(config, registry, bus, workspace: Path):
    (workspace / "a.py").write_text("x\n", encoding="utf-8")
    client = FakeClient([reply("读了", [call("read_file", {"path": "a.py"})])])
    agent = Agent(config, registry, client, bus)

    original = client.complete
    state = {"n": 0}

    def flaky(messages, tools):
        state["n"] += 1
        if state["n"] > 1:
            raise RuntimeError("网络断了")
        return original(messages, tools)

    client.complete = flaky  # type: ignore[assignment]
    result = agent.run("t")

    assert result.reason is StopReason.ERROR
    # 历史仍在：用户可以修好问题后继续，而不是从零开始
    assert any("x" in str(m.get("content")) for m in agent.conversation.to_messages())


# ------------------------------------------------------------------ 错误当输入


def test_tool_failure_does_not_terminate(config, registry, bus, workspace: Path):
    """工具失败是给模型的输入，不是要抛出的异常。"""
    (workspace / "right.py").write_text("x = 1\n", encoding="utf-8")
    agent, client = agent_with(
        [
            reply("", [call("read_file", {"path": "wrong.py"})]),
            reply("", [call("read_file", {"path": "right.py"})]),
            reply("找到了"),
        ],
        config, registry, bus,
    )
    result = agent.run("t")

    assert result.reason is StopReason.DONE
    assert any("不存在" in str(m.get("content")) for m in client.seen[1])


def test_parse_error_is_fed_back_without_executing(config, registry, bus, workspace: Path):
    agent, client = agent_with(
        [
            reply("", [call("write_file", {}, call_id="bad", error="arguments 不是合法 JSON")]),
            reply("我重发一次"),
        ],
        config, registry, bus,
    )
    result = agent.run("t")

    assert result.reason is StopReason.DONE
    assert any("参数解析失败" in str(m.get("content")) for m in client.seen[1])
    assert list(workspace.iterdir()) == []


def test_parallel_calls_in_one_turn_all_answered(config, registry, bus, workspace: Path):
    """一轮里多个工具调用，每个都得有配对结果。"""
    for name in ("a", "b"):
        (workspace / f"{name}.py").write_text("x\n", encoding="utf-8")
    agent, _ = agent_with(
        [
            reply("", [
                call("read_file", {"path": "a.py"}, call_id="c1"),
                call("read_file", {"path": "b.py"}, call_id="c2"),
            ]),
            reply("done"),
        ],
        config, registry, bus,
    )
    agent.run("t")
    ids = {m["tool_call_id"] for m in agent.conversation.to_messages() if m["role"] == "tool"}
    assert ids == {"c1", "c2"}


# ------------------------------------------------------------------ 失效联动


def test_write_invalidates_read_in_next_request(config, registry, bus, workspace: Path):
    """端到端验证：读 → 写 → 下一轮请求里旧内容必须已经消失。

    这是整个上下文管理的核心断言。路径写法故意不一致（./a.py vs a.py），
    因为规范化失手时这个机制会静默失灵。
    """
    (workspace / "a.py").write_text("def old_name(): pass\n", encoding="utf-8")
    agent, client = agent_with(
        [
            reply("", [call("read_file", {"path": "./a.py"}, call_id="r1")]),
            reply("", [call("write_file", {"path": "a.py", "content": "def new_name(): pass\n"}, call_id="w1")]),
            reply("改完了"),
        ],
        config, registry, bus,
    )
    result = agent.run("重命名函数")

    assert result.reason is StopReason.DONE
    first_request, last_request = client.seen[0], client.seen[-1]
    assert not any("old_name" in str(m.get("content")) for m in last_request), \
        "写操作后旧读取内容仍在上下文里，失效机制没生效"
    assert any("已被修改" in str(m.get("content")) for m in last_request)
    assert len(last_request) > len(first_request)


def test_read_after_write_is_kept(config, registry, bus, workspace: Path):
    """写之后的读是新的，不该被后来的失效误伤。"""
    (workspace / "a.py").write_text("v1\n", encoding="utf-8")
    agent, client = agent_with(
        [
            reply("", [call("write_file", {"path": "a.py", "content": "v2\n"}, call_id="w1")]),
            reply("", [call("read_file", {"path": "a.py"}, call_id="r1")]),
            reply("确认了"),
        ],
        config, registry, bus,
    )
    agent.run("t")
    assert any("v2" in str(m.get("content")) for m in client.seen[-1])


# ------------------------------------------------------------------ 事件流


def test_events_reach_subscribers(config, registry, bus, workspace: Path):
    (workspace / "a.py").write_text("x\n", encoding="utf-8")
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(e.kind))

    agent, _ = agent_with(
        [reply("", [call("read_file", {"path": "a.py"})]), reply("done")],
        config, registry, bus,
    )
    agent.run("t")

    assert seen[0] == "run_started"
    assert seen[-1] == "run_finished"
    for kind in ("turn_started", "tool_call_started", "tool_call_finished", "assistant_text"):
        assert kind in seen


def test_failed_tool_reported_as_finished_not_error(config, registry, bus):
    """工具失败在事件流里也是 tool_call_finished(ok=False)，不是 agent_error。
    渲染层据此把它画成正常流程的一部分，而不是崩溃。"""
    finished: list[ToolCallFinished] = []
    bus.subscribe(lambda e: finished.append(e) if isinstance(e, ToolCallFinished) else None)

    agent, _ = agent_with(
        [reply("", [call("read_file", {"path": "nope.py"})]), reply("done")],
        config, registry, bus,
    )
    agent.run("t")

    assert len(finished) == 1
    assert finished[0].ok is False


def test_subscriber_exception_cannot_kill_the_run(config, registry, bus):
    """渲染出错不该让 agent 死掉 —— 事件总线刻意吞掉订阅者异常。"""
    def broken(event):
        raise ValueError("渲染炸了")

    bus.subscribe(broken)
    agent, _ = agent_with([reply("done")], config, registry, bus)
    assert agent.run("t").reason is StopReason.DONE


# ------------------------------------------------------------------ 多轮会话


def test_second_task_shares_history(config, registry, bus):
    """交互模式下同一个 Agent 跑多个任务，上文得留着。"""
    agent, client = agent_with([reply("第一个做完了"), reply("第二个做完了")], config, registry, bus)
    agent.run("任务一")
    agent.run("任务二")

    users = [m for m in client.seen[-1] if m["role"] == "user"]
    assert [m["content"] for m in users] == ["任务一", "任务二"]
