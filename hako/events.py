"""事件总线：内核与呈现层之间的唯一接口。

设计决策（见 DESIGN.md #1）：内核不做任何渲染，只 emit 事件。
TUI 渲染器、评测框架（headless）、未来可能的 Web 面板都只是订阅者。

代价：多一层间接。
收益：评测跑的内核和用户跑的内核是同一个，不存在"演示能过、评测不能过"的分叉；
      加 UI 不需要改内核一行代码。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------- 事件定义
# 用 frozen dataclass 而不是 dict：字段拼错会立刻炸，而不是在渲染层静默取到 None。


@dataclass(frozen=True)
class Event:
    """所有事件的基类。kind 用于订阅者分派。"""

    kind: str = field(init=False, default="event")


@dataclass(frozen=True)
class RunStarted(Event):
    task: str
    model: str
    cwd: str
    kind: str = field(init=False, default="run_started")


@dataclass(frozen=True)
class TurnStarted(Event):
    """一次 LLM 请求前。step 从 1 开始。"""

    step: int
    max_steps: int
    kind: str = field(init=False, default="turn_started")


@dataclass(frozen=True)
class AssistantText(Event):
    """模型的自然语言输出（非工具调用部分）。"""

    text: str
    kind: str = field(init=False, default="assistant_text")


@dataclass(frozen=True)
class ToolCallStarted(Event):
    call_id: str
    name: str
    args: dict[str, Any]
    kind: str = field(init=False, default="tool_call_started")


@dataclass(frozen=True)
class ToolCallFinished(Event):
    """ok=False 表示工具失败——注意这**不**终止循环，错误会回传给模型自我修正。"""

    call_id: str
    name: str
    ok: bool
    summary: str          # 一行摘要，给 TUI 显示
    detail: str           # 回传给模型的完整（已截断）结果
    duration_ms: int
    touched_paths: tuple[str, ...] = ()
    created_paths: tuple[str, ...] = ()
    modified_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    derived_paths: tuple[str, ...] = ()
    verification_kind: str = ""
    verification_command: str = ""
    command_status: str = ""
    exit_code: int | None = None
    kind: str = field(init=False, default="tool_call_finished")


@dataclass(frozen=True)
class ContextStats(Event):
    """每轮请求后的上下文占用，驱动 TUI 顶部那条占用条。"""

    used_tokens: int
    limit: int
    message_count: int
    kind: str = field(init=False, default="context_stats")


@dataclass(frozen=True)
class VerificationRequired(Event):
    """模型试图在修改后无验证结束；内核已要求它继续。"""

    changed_paths: tuple[str, ...]
    message: str
    kind: str = field(init=False, default="verification_required")


@dataclass(frozen=True)
class ContinuationRequired(Event):
    """模型输出被截断且未调用工具；内核拒绝把它当成完成。"""

    attempt: int
    max_attempts: int
    finish_reason: str
    message: str
    kind: str = field(init=False, default="continuation_required")


@dataclass(frozen=True)
class SubagentStarted(Event):
    """主 Agent 启动一次隔离的只读调查。"""

    task: str
    max_steps: int
    kind: str = field(init=False, default="subagent_started")


@dataclass(frozen=True)
class SubagentFinished(Event):
    """只读调查的独立成本与上下文峰值。"""

    ok: bool
    reason: str
    steps: int
    total_tokens: int
    max_context_tokens: int
    kind: str = field(init=False, default="subagent_finished")


@dataclass(frozen=True)
class RunFinished(Event):
    reason: str           # 见 loop.StopReason
    steps: int
    total_tokens: int
    changed_paths: tuple[str, ...] = ()
    verification: str = ""
    kind: str = field(init=False, default="run_finished")


@dataclass(frozen=True)
class AgentError(Event):
    """不可恢复的错误（API 401、用户拒绝授权、磁盘满）。可恢复的错误走 ToolCallFinished。"""

    message: str
    fatal: bool = True
    kind: str = field(init=False, default="agent_error")


# ---------------------------------------------------------------- 总线


class EventBus:
    """同步事件总线。

    刻意保持同步：agent 循环本身是顺序的，引入 async 只会让"某个工具卡住时
    UI 还在动"这种伪需求带来真复杂度。渲染慢是渲染层自己的问题。
    """

    def __init__(self) -> None:
        self._subscribers: list[Callable[[Event], None]] = []
        self.failures = 0
        self._warned = False

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        self._subscribers.append(handler)

    def emit(self, event: Event) -> None:
        for handler in self._subscribers:
            # 订阅者抛异常不能连带炸掉 agent 循环——渲染失败远没有任务失败严重。
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                self._report(exc)

    def _report(self, exc: Exception) -> None:
        """吞掉异常，但只吞一次就说一声。

        纯 `pass` 吃过一次教训：渲染器在 cp936 控制台上编码不了 ◇，
        每个事件都抛 UnicodeEncodeError，于是**整个 transcript 静默消失**，
        而 agent 明明跑得很好。不崩是对的，一声不响不是。
        往 stderr 写而不进事件流：报告渲染故障的通道不能是出故障的那条。
        """
        self.failures += 1
        if self._warned:
            return
        self._warned = True
        print(
            f"[hako] 渲染订阅者出错，已忽略并继续执行：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
