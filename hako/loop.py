"""agent 主循环与终止条件。

设计决策（见 DESIGN.md #2）：终止条件是**分层**的，因为失败模式不同：

1. DONE      模型不再请求工具 → 正常完成
2. MAX_STEPS 步数上限 → 防失控
3. STUCK     无进展检测 → 同一调用重复出现，比步数上限更早生效
4. DENIED    用户拒绝授权 → 人的决定，不重试
5. ERROR     不可恢复错误（API 401、消息结构非法）

只有 1 是成功。单一的步数上限不够用：卡死在同一个调用上时，
烧满 40 步才停既浪费钱又浪费用户时间。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from . import events as ev
from .config import Config
from .history import Conversation
from .llm import LLMClient
from .prompt import build_system_prompt
from .tools import Registry, Tool

# 同一调用重复到第 N 次就判定卡死。取 3 而不是 2：
# 第 2 次可能是模型在重试一个瞬时失败（文件锁、端口占用），这是合理行为。
STUCK_THRESHOLD = 3

ApprovalFn = Callable[[Tool, dict], bool]


class StopReason(str, Enum):
    DONE = "done"
    MAX_STEPS = "max_steps"
    STUCK = "stuck"
    DENIED = "denied"
    ERROR = "error"


@dataclass
class RunResult:
    reason: StopReason
    steps: int
    total_tokens: int
    final_text: str = ""

    @property
    def ok(self) -> bool:
        return self.reason is StopReason.DONE


class Agent:
    def __init__(
        self,
        config: Config,
        registry: Registry,
        client: LLMClient,
        bus: ev.EventBus,
        approve: ApprovalFn | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.client = client
        self.bus = bus
        # 默认全部批准：评测跑 headless 时没有人可问。
        # 交互模式由 CLI 传入真正会询问用户的实现。
        self.approve = approve or (lambda tool, args: True)
        self.conversation = Conversation(
            system_prompt=build_system_prompt(config.workspace, registry.names())
        )

    # ------------------------------------------------------------ 主循环

    def run(self, task: str) -> RunResult:
        self.bus.emit(
            ev.RunStarted(task=task, model=self.config.model, cwd=str(self.config.workspace))
        )
        self.conversation.add_user(task)

        call_counts: dict[str, int] = {}
        final_text = ""
        step = 0

        while step < self.config.max_steps:
            step += 1
            self.bus.emit(ev.TurnStarted(step=step, max_steps=self.config.max_steps))

            try:
                reply = self.client.complete(
                    self.conversation.to_messages(), self.registry.schemas()
                )
            except Exception as exc:  # noqa: BLE001
                self.bus.emit(ev.AgentError(message=str(exc)))
                return self._finish(StopReason.ERROR, step, final_text)

            self.conversation.total_prompt_tokens += reply.prompt_tokens
            self.conversation.total_completion_tokens += reply.completion_tokens
            self.bus.emit(
                ev.ContextStats(
                    used_tokens=reply.prompt_tokens or self.conversation.estimated_tokens(),
                    limit=self.config.context_limit,
                    message_count=len(self.conversation.turns),
                )
            )

            if reply.text:
                self.bus.emit(ev.AssistantText(text=reply.text))
                final_text = reply.text

            # 终止条件 1：没有工具调用，说明它认为做完了
            if not reply.calls:
                self.conversation.add_assistant(reply.text, [])
                return self._finish(StopReason.DONE, step, final_text)

            self.conversation.add_assistant(reply.text, reply.calls)

            stop = self._execute_calls(reply.calls, call_counts)
            if stop is not None:
                return self._finish(stop, step, final_text)

        return self._finish(StopReason.MAX_STEPS, step, final_text)

    # ------------------------------------------------------------ 工具执行

    def _execute_calls(self, calls: list, call_counts: dict[str, int]) -> StopReason | None:
        """执行一轮里的所有工具调用。返回非 None 表示应当终止。"""
        for call in calls:
            # 解析失败：不执行，把错误当作工具结果回传，让模型重发
            if call.parse_error:
                self.conversation.add_tool_result(
                    call.call_id,
                    call.name,
                    f"参数解析失败：{call.parse_error}\n请重新调用，确保 arguments 是合法 JSON 对象。",
                )
                continue

            tool = self.registry.get(call.name)

            # 终止条件 3：无进展检测
            signature = f"{call.name}:{json.dumps(call.args, sort_keys=True, ensure_ascii=False)}"
            call_counts[signature] = call_counts.get(signature, 0) + 1
            repeats = call_counts[signature]
            if repeats >= STUCK_THRESHOLD:
                self.conversation.add_tool_result(
                    call.call_id, call.name, "检测到重复调用，已终止。"
                )
                return StopReason.STUCK

            # 终止条件 4：用户拒绝
            if tool is not None and tool.needs_approval and not self.approve(tool, call.args):
                self.conversation.add_tool_result(
                    call.call_id, call.name, "用户拒绝了该操作。"
                )
                return StopReason.DENIED

            self.bus.emit(
                ev.ToolCallStarted(call_id=call.call_id, name=call.name, args=call.args)
            )

            started = time.perf_counter()
            result = self.registry.invoke(call.name, call.args)
            duration_ms = int((time.perf_counter() - started) * 1000)

            detail = result.detail
            # 阶梯式干预：第 2 次重复同一调用时先提醒，而不是直接判死。
            if repeats == STUCK_THRESHOLD - 1:
                detail += (
                    "\n\n[系统提示] 你已经用完全相同的参数调用过这个工具。"
                    "换个做法或重新读取相关文件，不要再重复同一次调用。"
                )

            # 优先用工具自己声明的规范化路径；没有才退回模型给的原始字符串。
            self.conversation.add_tool_result(
                call.call_id,
                call.name,
                detail,
                path=result.subject_path or str(call.args.get("path", "")),
            )

            # 写操作后让该文件的历史读取失效
            if result.touched_paths:
                self.conversation.invalidate_reads(result.touched_paths)

            self.bus.emit(
                ev.ToolCallFinished(
                    call_id=call.call_id,
                    name=call.name,
                    ok=result.ok,
                    summary=result.summary,
                    detail=detail,
                    duration_ms=duration_ms,
                )
            )
        return None

    # ------------------------------------------------------------ 收尾

    def _finish(self, reason: StopReason, steps: int, final_text: str) -> RunResult:
        total = (
            self.conversation.total_prompt_tokens
            + self.conversation.total_completion_tokens
        )
        self.bus.emit(ev.RunFinished(reason=reason.value, steps=steps, total_tokens=total))
        return RunResult(reason=reason, steps=steps, total_tokens=total, final_text=final_text)
