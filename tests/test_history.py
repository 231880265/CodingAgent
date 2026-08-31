"""对话历史与陈旧读取失效。

这是整个上下文管理里最关键的一条机制，也是最容易静默失灵的一条：
路径字符串比不上就什么都不会发生，而且不报错。所以这里专门测匹配。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from hako.history import PLACEHOLDER, Conversation


@dataclass
class FakeCall:
    call_id: str
    name: str
    args: dict[str, Any]


def convo() -> Conversation:
    return Conversation(system_prompt="SYS")


def test_system_prompt_is_first_message():
    messages = convo().to_messages()
    assert messages[0] == {"role": "system", "content": "SYS"}


def test_tool_call_and_result_are_paired():
    """每个 tool_call 必须有配对的 tool 消息，否则下一轮请求被 API 拒。"""
    c = convo()
    c.add_user("task")
    c.add_assistant("", [FakeCall("id1", "read_file", {"path": "a.py"})])
    c.add_tool_result("id1", "read_file", "content", path="a.py")

    messages = c.to_messages()
    call_ids = {
        tc["id"]
        for m in messages
        if m.get("tool_calls")
        for tc in m["tool_calls"]
    }
    result_ids = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    assert call_ids == result_ids == {"id1"}


def test_assistant_stores_executed_arguments_not_raw_string():
    """存的是我们实际执行的那份参数。否则重放历史会和当时的行为分叉。"""
    c = convo()
    c.add_assistant("", [FakeCall("id1", "read_file", {"path": "a.py", "limit": 10})])
    arguments = c.to_messages()[-1]["tool_calls"][0]["function"]["arguments"]
    assert '"limit": 10' in arguments


def test_metadata_never_reaches_the_model():
    """tool_name / tool_path / stale 是内核自用的，不能出现在消息里。"""
    c = convo()
    c.add_tool_result("id1", "read_file", "x", path="a.py")
    message = c.to_messages()[-1]
    assert set(message) == {"role", "tool_call_id", "content"}


# ------------------------------------------------------------ 跨 Worker 恢复


def test_restore_semantic_rebuilds_user_assistant_pairs_only():
    c = convo()
    c.restore_semantic(
        [
            {"role": "user", "content": "先定位发布问题"},
            {"role": "assistant", "content": "根因是线上版本指针未持久化。"},
            {"role": "user", "content": "再补回归测试"},
            {"role": "assistant", "content": "已补测试并验证通过。"},
        ]
    )

    messages = c.to_messages()
    assert messages[0] == {"role": "system", "content": "SYS"}
    assert [message["role"] for message in messages[1:]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert all("tool_calls" not in message for message in messages)
    assert all(message.get("role") != "tool" for message in messages)


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "assistant", "content": "没有对应用户输入"}],
        [{"role": "user", "content": "没有回答"}],
        [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": ""},
        ],
    ],
)
def test_restore_semantic_rejects_incomplete_or_disordered_history(messages):
    with pytest.raises(ValueError):
        convo().restore_semantic(messages)


def test_restore_semantic_requires_an_empty_conversation():
    c = convo()
    c.add_user("当前 Worker 已有消息")
    with pytest.raises(ValueError, match="空 Conversation"):
        c.restore_semantic([])


def test_follow_up_compaction_keeps_recent_pairs_and_drops_tool_observations():
    c = convo()
    for index in range(10):
        c.add_user(f"goal {index}")
        c.add_assistant("", [FakeCall(f"read-{index}", "read_file", {"path": "a.py"})])
        c.add_tool_result(f"read-{index}", "read_file", f"old source {index}", path="a.py")
        c.add_assistant(f"answer {index}", [])

    c.compact_for_follow_up(memory_context="run facts", recent_pairs=3)

    messages = c.to_messages()
    assert [message["content"] for message in messages if message["role"] == "user"] == [
        "goal 7",
        "goal 8",
        "goal 9",
    ]
    assert all(message["role"] != "tool" for message in messages)
    assert all("old source" not in str(message.get("content")) for message in messages)
    assert messages[0]["role"] == "system"
    assert "run facts" in messages[0]["content"]


def test_follow_up_compaction_does_not_turn_kernel_nudge_into_user_goal():
    c = convo()
    c.add_user("fix checkout bug")
    c.add_assistant("I changed the repository.", [])
    c.add_user("run a verification after the latest change", semantic=False)
    c.add_assistant("Tests pass.", [])

    c.compact_for_follow_up()

    messages = c.to_messages()
    assert [message["content"] for message in messages if message["role"] == "user"] == [
        "fix checkout bug"
    ]
    assert messages[-1] == {"role": "assistant", "content": "Tests pass."}


# ------------------------------------------------------------------ 失效


def test_write_invalidates_earlier_read_of_same_file():
    c = convo()
    c.add_tool_result("r1", "read_file", "def old(): pass", path="src/a.py")
    assert c.invalidate_reads(("src/a.py",)) == 1

    content = c.to_messages()[-1]["content"]
    assert content == PLACEHOLDER
    assert "old" not in content
    # 占位符必须告诉模型怎么拿到当前内容，否则它只会照旧版本瞎猜
    assert "read_file" in content


def test_unrelated_file_untouched():
    c = convo()
    c.add_tool_result("r1", "read_file", "AAA", path="src/a.py")
    c.add_tool_result("r2", "read_file", "BBB", path="src/b.py")
    assert c.invalidate_reads(("src/a.py",)) == 1
    assert "BBB" in c.to_messages()[-1]["content"]


def test_multiple_reads_of_same_file_all_invalidated():
    c = convo()
    c.add_tool_result("r1", "read_file", "v1", path="a.py")
    c.add_tool_result("r2", "read_file", "v2", path="a.py")
    assert c.invalidate_reads(("a.py",)) == 2


def test_invalidation_is_idempotent():
    """第二次写同一个文件不该重复计数 —— stale 标记就是为这个。"""
    c = convo()
    c.add_tool_result("r1", "read_file", "v1", path="a.py")
    assert c.invalidate_reads(("a.py",)) == 1
    assert c.invalidate_reads(("a.py",)) == 0


def test_only_read_file_results_are_invalidated():
    """run_command 的输出不是文件快照，不该被当成陈旧读取抹掉。"""
    c = convo()
    c.add_tool_result("c1", "run_command", "pytest 输出", path="")
    c.add_tool_result("r1", "read_file", "src", path="a.py")
    assert c.invalidate_reads(("a.py", "")) == 1
    assert "pytest 输出" in c.to_messages()[-2]["content"]


def test_user_and_assistant_messages_never_invalidated():
    c = convo()
    c.add_user("请修改 a.py")
    c.add_assistant("好的", [])
    assert c.invalidate_reads(("a.py",)) == 0


def test_multiple_touched_paths_in_one_write():
    c = convo()
    c.add_tool_result("r1", "read_file", "A", path="a.py")
    c.add_tool_result("r2", "read_file", "B", path="b.py")
    assert c.invalidate_reads(("a.py", "b.py")) == 2


def test_invalidation_shrinks_context():
    """省 token 是这个机制的副作用，但也是真实收益，值得断言。"""
    c = convo()
    c.add_tool_result("r1", "read_file", "x" * 20000, path="a.py")
    before = c.estimated_tokens()
    c.invalidate_reads(("a.py",))
    assert c.estimated_tokens() < before / 4


def test_estimated_tokens_includes_tool_call_arguments():
    c = convo()
    base = c.estimated_tokens()
    c.add_assistant("", [FakeCall("id1", "write_file", {"content": "x" * 4000})])
    assert c.estimated_tokens() > base + 500
