"""主循环与终止条件。

这里测的是"什么时候停、为什么停"。正常完成、未验证完成和各类失败原因
分别验证，而不是笼统测一个"能跑完"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hako.events import (
    ContinuationRequired,
    EventBus,
    ToolCallFinished,
    VerificationRequired,
)
from hako.loop import MAX_CONTINUATION_NUDGES, STUCK_THRESHOLD, Agent, StopReason
from hako.tools import ToolResult
from tests.fake_llm import ExplodingClient, FakeClient, call, reply


def agent_with(script, config, registry, bus, approve=None) -> tuple[Agent, FakeClient]:
    client = FakeClient(script)
    policy = approve if approve is not None else (lambda tool, args: True)
    return Agent(config, registry, client, bus, approve=policy), client


def fake_verifier(registry, outcomes: list[bool], kind: str = "test") -> None:
    """把真实 shell 替换成带结构化证据的快速验证器。"""
    tool = registry.get("run_command")
    assert tool is not None
    remaining = list(outcomes)

    def handler(command: str, timeout: int = 60) -> ToolResult:
        ok = remaining.pop(0)
        return ToolResult(
            ok=ok,
            detail=f"exit={0 if ok else 1}\n{'2 passed' if ok else '1 failed'}",
            summary=f"{command}  exit={0 if ok else 1}",
            verification_kind=kind,
            verification_command=command,
        )

    tool.handler = handler


# ------------------------------------------------------------------ DONE


def test_no_tool_calls_means_done(config, registry, bus):
    agent, client = agent_with([reply("已完成")], config, registry, bus)
    result = agent.run("任务")

    assert result.reason is StopReason.DONE_READ_ONLY
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

    assert result.reason is StopReason.DONE_READ_ONLY
    assert result.ok
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


def test_truncated_no_tool_reply_is_nudged_then_can_act(
    config, registry, bus, workspace: Path
):
    continued: list[ContinuationRequired] = []
    bus.subscribe(
        lambda event: continued.append(event)
        if isinstance(event, ContinuationRequired)
        else None
    )
    fake_verifier(registry, [True])
    agent, client = agent_with(
        [
            reply(
                "我先长篇分析但还没开始修改……",
                finish_reason="length",
                completion_tokens=config.max_output_tokens,
            ),
            reply(
                "",
                [call("write_file", {"path": "a.py", "content": "x = 1\n"})],
            ),
            reply("", [call("run_command", {"command": "pytest -q"})]),
            reply("已修改并验证"),
        ],
        config,
        registry,
        bus,
    )

    result = agent.run("创建并测试 a.py")

    assert result.reason is StopReason.DONE_VERIFIED
    assert (workspace / "a.py").exists()
    assert len(continued) == 1
    assert "请立即调用" in str(client.seen[1])


def test_repeated_truncated_replies_never_count_as_read_only_done(
    config, registry, bus
):
    agent, _ = agent_with(
        [
            reply("仍在分析", finish_reason="length")
            for _ in range(MAX_CONTINUATION_NUDGES + 1)
        ],
        config,
        registry,
        bus,
    )

    result = agent.run("修复问题")

    assert result.reason is StopReason.INCOMPLETE
    assert not result.ok
    assert result.steps == MAX_CONTINUATION_NUDGES + 1


# ------------------------------------------------------- VERIFIED FINISH


def test_write_without_verification_is_nudged_then_unverified(
    config, registry, bus, workspace: Path
):
    required: list[VerificationRequired] = []
    bus.subscribe(
        lambda event: required.append(event)
        if isinstance(event, VerificationRequired)
        else None
    )
    agent, client = agent_with(
        [
            reply("", [call("write_file", {"path": "a.py", "content": "x = 1\n"})]),
            reply("已经改完"),
            reply("当前无法验证"),
        ],
        config,
        registry,
        bus,
    )

    result = agent.run("创建 a.py")

    assert result.reason is StopReason.DONE_UNVERIFIED
    assert not result.ok
    assert result.changed_paths == ("a.py",)
    assert result.verification == ()
    assert len(required) == 1
    assert "最后一次文件修改后" in str(client.seen[2])


def test_successful_check_after_last_write_is_verified(
    config, registry, bus, workspace: Path
):
    fake_verifier(registry, [True])
    agent, _ = agent_with(
        [
            reply("", [call("write_file", {"path": "a.py", "content": "x = 1\n"})]),
            reply("", [call("run_command", {"command": "pytest -q"})]),
            reply("修复完成，测试通过"),
        ],
        config,
        registry,
        bus,
    )

    result = agent.run("创建并测试")

    assert result.reason is StopReason.DONE_VERIFIED
    assert result.ok
    assert result.changed_paths == ("a.py",)
    assert len(result.verification) == 1
    assert result.verification[0].kind == "test"
    assert result.verification[0].command == "pytest -q"
    assert result.verification[0].step == 2


def test_build_artifact_does_not_invalidate_the_build_evidence(
    config, registry, bus, workspace: Path
):
    """真实 C++ 场景：源码是交付变更，exe 是可审计产物，不是新一轮源码修改。"""
    tool = registry.get("run_command")
    assert tool is not None

    def compile_ok(command: str, timeout: int = 60) -> ToolResult:
        (workspace / "qsort.exe").write_bytes(b"compiled")
        return ToolResult(
            ok=True,
            detail="exit=0\ncompiled",
            summary=f"{command}  exit=0 · files +1 ~0 -0",
            touched_paths=("qsort.exe",),
            created_paths=("qsort.exe",),
            derived_paths=("qsort.exe",),
            verification_kind="build",
            verification_command=command,
        )

    tool.handler = compile_ok
    finished: list[ToolCallFinished] = []
    bus.subscribe(
        lambda event: finished.append(event)
        if isinstance(event, ToolCallFinished)
        else None
    )
    agent, _ = agent_with(
        [
            reply(
                "",
                [
                    call(
                        "write_file",
                        {"path": "qsort.cpp", "content": "int main() { return 0; }\n"},
                    )
                ],
            ),
            reply(
                "",
                [call("run_command", {"command": "g++ qsort.cpp -o qsort.exe"})],
            ),
            reply("编译验证完成"),
        ],
        config,
        registry,
        bus,
    )

    result = agent.run("编写并验证 C++ 程序")

    assert result.reason is StopReason.DONE_VERIFIED
    assert result.changed_paths == ("qsort.cpp",)
    assert result.verification[0].kind == "build"
    assert finished[-1].derived_paths == ("qsort.exe",)
    assert finished[-1].verification_kind == "build"


def test_failed_check_does_not_verify(config, registry, bus):
    fake_verifier(registry, [False])
    agent, _ = agent_with(
        [
            reply("", [call("write_file", {"path": "a.py", "content": "bad\n"})]),
            reply("", [call("run_command", {"command": "pytest -q"})]),
            reply("我先结束"),
            reply("无法继续"),
        ],
        config,
        registry,
        bus,
    )
    result = agent.run("t")
    assert result.reason is StopReason.DONE_UNVERIFIED
    assert result.verification == ()


def test_verification_before_write_does_not_count(config, registry, bus):
    fake_verifier(registry, [True])
    agent, _ = agent_with(
        [
            reply("", [call("run_command", {"command": "pytest -q"})]),
            reply("", [call("write_file", {"path": "a.py", "content": "new\n"})]),
            reply("完成"),
            reply("没有后续验证"),
        ],
        config,
        registry,
        bus,
    )
    result = agent.run("t")
    assert result.reason is StopReason.DONE_UNVERIFIED
    assert result.verification == ()


def test_later_write_invalidates_successful_verification(config, registry, bus):
    fake_verifier(registry, [True])
    agent, _ = agent_with(
        [
            reply("", [call("write_file", {"path": "a.py", "content": "v1\n"})]),
            reply("", [call("run_command", {"command": "pytest -q"})]),
            reply("", [call("write_file", {"path": "a.py", "content": "v2\n"})]),
            reply("完成"),
            reply("没有再测"),
        ],
        config,
        registry,
        bus,
    )
    result = agent.run("t")
    assert result.reason is StopReason.DONE_UNVERIFIED
    assert result.verification == ()


def test_later_failed_check_clears_earlier_success(config, registry, bus):
    fake_verifier(registry, [True, False])
    agent, _ = agent_with(
        [
            reply("", [call("write_file", {"path": "a.py", "content": "v1\n"})]),
            reply("", [call("run_command", {"command": "pytest -q"})]),
            reply("", [call("run_command", {"command": "ruff check ."})]),
            reply("结束"),
            reply("检查仍失败"),
        ],
        config,
        registry,
        bus,
    )
    result = agent.run("t")
    assert result.reason is StopReason.DONE_UNVERIFIED
    assert result.verification == ()


def test_verification_state_resets_between_interactive_tasks(config, registry, bus):
    fake_verifier(registry, [True])
    agent, _ = agent_with(
        [
            reply("", [call("write_file", {"path": "a.py", "content": "v1\n"})]),
            reply("", [call("run_command", {"command": "pytest -q"})]),
            reply("第一个任务完成"),
            reply("第二个任务只做说明"),
        ],
        config,
        registry,
        bus,
    )
    first = agent.run("任务一")
    second = agent.run("任务二")
    assert first.reason is StopReason.DONE_VERIFIED
    assert second.reason is StopReason.DONE_READ_ONLY


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


def test_successful_edit_starts_a_new_stuck_detection_phase(
    config, registry, bus, workspace: Path
):
    """同一文件在真实修改后是新状态，允许重新读取确认。"""
    (workspace / "a.py").write_text("value = 1\n", encoding="utf-8")
    fake_verifier(registry, [True])
    read = lambda call_id: call("read_file", {"path": "a.py"}, call_id=call_id)
    agent, _ = agent_with(
        [
            reply("", [read("r1")]),
            reply("", [read("r2")]),
            reply(
                "",
                [
                    call(
                        "edit_file",
                        {
                            "path": "a.py",
                            "old_text": "value = 1",
                            "new_text": "value = 2",
                        },
                        call_id="e1",
                    )
                ],
            ),
            reply("", [read("r3")]),
            reply("", [call("run_command", {"command": "pytest -q"})]),
            reply("已修改并验证"),
        ],
        config,
        registry,
        bus,
    )

    result = agent.run("修改后重新读取并验证")

    assert result.reason is StopReason.DONE_VERIFIED
    assert (workspace / "a.py").read_text(encoding="utf-8") == "value = 2\n"


def test_failed_edit_does_not_reset_stuck_detection(
    config, registry, bus, workspace: Path
):
    """失败且没有文件副作用的编辑不是进展，第三次相同读取仍应止损。"""
    (workspace / "a.py").write_text("value = 1\n", encoding="utf-8")
    read = lambda call_id: call("read_file", {"path": "a.py"}, call_id=call_id)
    agent, _ = agent_with(
        [
            reply("", [read("r1")]),
            reply("", [read("r2")]),
            reply(
                "",
                [
                    call(
                        "edit_file",
                        {
                            "path": "a.py",
                            "old_text": "missing = 0",
                            "new_text": "value = 2",
                        },
                        call_id="e1",
                    )
                ],
            ),
            reply("", [read("r3")]),
        ],
        config,
        registry,
        bus,
    )

    result = agent.run("失败编辑后仍重复读取")

    assert result.reason is StopReason.STUCK
    assert (workspace / "a.py").read_text(encoding="utf-8") == "value = 1\n"


def test_run_command_file_effect_starts_a_new_stuck_detection_phase(
    config, registry, bus, workspace: Path
):
    """shell 即使退出非零，只要产生净文件变化，也已经进入新工作区状态。"""
    (workspace / "a.py").write_text("value = 1\n", encoding="utf-8")
    tool = registry.get("run_command")
    assert tool is not None

    def command_with_effect(command: str, timeout: int = 60) -> ToolResult:
        if command == "python mutate.py":
            (workspace / "a.py").write_text("value = 2\n", encoding="utf-8")
            return ToolResult(
                ok=False,
                detail="exit=1\nmutation happened before failure",
                summary="python mutate.py  exit=1 · files +0 ~1 -0",
                touched_paths=("a.py",),
                modified_paths=("a.py",),
            )
        return ToolResult(
            ok=True,
            detail="exit=0\n1 passed",
            summary=f"{command}  exit=0",
            verification_kind="test",
            verification_command=command,
        )

    tool.handler = command_with_effect
    read = lambda call_id: call("read_file", {"path": "a.py"}, call_id=call_id)
    agent, _ = agent_with(
        [
            reply("", [read("r1")]),
            reply("", [read("r2")]),
            reply("", [call("run_command", {"command": "python mutate.py"})]),
            reply("", [read("r3")]),
            reply("", [call("run_command", {"command": "pytest -q"})]),
            reply("已确认并验证"),
        ],
        config,
        registry,
        bus,
    )

    result = agent.run("命令修改文件后重新读取")

    assert result.reason is StopReason.DONE_VERIFIED
    assert (workspace / "a.py").read_text(encoding="utf-8") == "value = 2\n"


# ------------------------------------------------------- APPROVAL OBSERVATION


def test_user_denial_is_observation_without_executing(config, registry, bus, workspace: Path):
    """拒绝一次工具调用不等于取消 Run；模型仍可给出只读结论。"""
    agent, _ = agent_with(
        [
            reply("", [call("write_file", {"path": "x.py", "content": "boom"})]),
            reply("写入被拒绝，未修改工作区"),
        ],
        config, registry, bus,
        approve=lambda tool, args: False,
    )
    result = agent.run("写个文件")

    assert result.reason is StopReason.DONE_READ_ONLY
    assert result.steps == 2
    assert not (workspace / "x.py").exists()


def test_library_default_denies_tools_that_need_approval(
    config, registry, bus, workspace: Path
):
    client = FakeClient(
        [
            reply("", [call("write_file", {"path": "x.py", "content": "boom"})]),
            reply("默认策略拒绝了写入，工作区未修改"),
        ]
    )
    agent = Agent(config, registry, client, bus)

    result = agent.run("写个文件")

    assert result.reason is StopReason.DONE_READ_ONLY
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

    assert result.reason is StopReason.DONE_UNVERIFIED
    assert not result.ok
    first_request, last_request = client.seen[0], client.seen[-1]
    assert not any("old_name" in str(m.get("content")) for m in last_request), \
        "写操作后旧读取内容仍在上下文里，失效机制没生效"
    assert any("已被修改" in str(m.get("content")) for m in last_request)
    assert len(last_request) > len(first_request)


def test_edit_invalidates_old_read_and_can_finish_verified(
    config, registry, bus, workspace: Path
):
    """edit_file 必须复用 write_file 的失效协议，并由后续测试关闭 DIRTY 状态。"""
    (workspace / "a.py").write_text("value = 'old'\n", encoding="utf-8")
    fake_verifier(registry, [True])
    agent, client = agent_with(
        [
            reply("", [call("read_file", {"path": "./a.py"}, call_id="r1")]),
            reply(
                "",
                [
                    call(
                        "edit_file",
                        {
                            "path": "a.py",
                            "old_text": "value = 'old'",
                            "new_text": "value = 'new'",
                        },
                        call_id="e1",
                    )
                ],
            ),
            reply("", [call("run_command", {"command": "pytest -q"}, call_id="t1")]),
            reply("已修复并验证"),
        ],
        config,
        registry,
        bus,
    )

    result = agent.run("更新值")

    assert result.reason is StopReason.DONE_VERIFIED
    assert (workspace / "a.py").read_text(encoding="utf-8") == "value = 'new'\n"
    request_after_edit = client.seen[2]
    assert not any("value = 'old'" in str(m.get("content")) for m in request_after_edit)
    assert any("已被修改" in str(m.get("content")) for m in request_after_edit)


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


def test_attachment_is_user_context_in_the_same_conversation(config, registry, bus):
    agent, client = agent_with(
        [reply("已看日志"), reply("继续处理")], config, registry, bus
    )
    agent.run("分析报错", attachment_context="<attachment name='error.log'>boom</attachment>")
    agent.run("结合上一轮继续")

    users = [m["content"] for m in client.seen[-1] if m["role"] == "user"]
    assert users[0].startswith("分析报错")
    assert "error.log" in users[0]
    assert "boom" in users[0]
    assert users[1] == "结合上一轮继续"


def test_cancel_before_model_call_stops_without_calling_model(config, registry, bus):
    cancelled = True
    client = FakeClient([reply("不应被调用")])
    agent = Agent(
        config,
        registry,
        client,
        bus,
        approve=lambda tool, args: True,
        cancelled=lambda: cancelled,
    )

    result = agent.run("取消这一轮")

    assert result.reason is StopReason.CANCELLED
    assert result.steps == 0
    assert client.calls_made == 0
