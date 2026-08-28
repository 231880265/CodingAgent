"""agent 主循环、分层终止条件与 Verified Finish。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from . import events as ev
from .config import Config
from .history import Conversation
from .llm import LLMClient, ModelReply
from .prompt import build_system_prompt
from .tools import Registry, Tool, ToolResult

# 同一调用重复到第 N 次就判定卡死。取 3 而不是 2：
# 第 2 次可能是模型在重试一个瞬时失败（文件锁、端口占用），这是合理行为。
STUCK_THRESHOLD = 3
# 截断后最多给两次“停止纸上分析，继续行动”的机会；再截断就明确失败，
# 不能伪装成 DONE_READ_ONLY，也不能无限烧 token。
MAX_CONTINUATION_NUDGES = 2

ApprovalFn = Callable[[Tool, dict], bool]


class StopReason(str, Enum):
    DONE_READ_ONLY = "done_read_only"
    # 兼容早期调用方；新代码应使用语义更明确的 DONE_READ_ONLY。
    DONE = "done_read_only"
    DONE_VERIFIED = "done_verified"
    DONE_UNVERIFIED = "done_unverified"
    INCOMPLETE = "incomplete"
    MAX_STEPS = "max_steps"
    STUCK = "stuck"
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True)
class VerificationEvidence:
    """一次发生在最终修改之后、由工具结果证明成功的验证。"""

    kind: str
    command: str
    summary: str
    step: int


@dataclass
class _RunState:
    changed_paths: set[str] = field(default_factory=set)
    evidence: list[VerificationEvidence] = field(default_factory=list)
    verification_nudged: bool = False
    continuation_nudges: int = 0

    def record_change(self, paths: tuple[str, ...]) -> None:
        self.changed_paths.update(paths)
        # 任何后续写入都会让先前验证过期。
        self.evidence.clear()
        self.verification_nudged = False

    def record_verification(
        self, *, ok: bool, kind: str, command: str, summary: str, step: int
    ) -> None:
        if not kind:
            return
        if not ok:
            # 最新验证失败说明当前版本不能继续沿用更早的“通过”结论。
            self.evidence.clear()
            return
        self.evidence.append(
            VerificationEvidence(
                kind=kind,
                command=command,
                summary=summary,
                step=step,
            )
        )

    @property
    def verified(self) -> bool:
        return bool(self.changed_paths and self.evidence)


@dataclass
class RunResult:
    reason: StopReason
    steps: int
    total_tokens: int
    final_text: str = ""
    changed_paths: tuple[str, ...] = ()
    verification: tuple[VerificationEvidence, ...] = ()

    @property
    def ok(self) -> bool:
        return self.reason in (StopReason.DONE_READ_ONLY, StopReason.DONE_VERIFIED)


VERIFICATION_REQUIRED = (
    "[hako 完成检查] 最后一次文件修改后还没有成功验证。"
    "请调用 run_command 执行一个单独的测试、构建或静态检查命令；"
    "不要用 `|| true`、管道或额外命令掩盖退出码。"
    "如果确实无法验证，请明确说明原因；再次直接结束会记为 DONE_UNVERIFIED。"
)

TRUNCATED_CONTINUATION_REQUIRED = (
    "[hako 截断检查] 上一条回复达到模型输出上限且没有工具调用，不能视为任务完成。"
    "不要重复长篇分析或只输出拟议代码；如果任务尚需修改，请立即调用下一个具体工具。"
    "如果确实是只读任务且已经完成，请用两句话给出最终结论。"
)


class Agent:
    def __init__(
        self,
        config: Config,
        registry: Registry,
        client: LLMClient,
        bus: ev.EventBus,
        approve: ApprovalFn | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.client = client
        self.bus = bus
        # 库调用默认拒绝有副作用的工具；CLI 会传入交互审批，评测若要 headless
        # 放行也必须显式传策略，避免“忘了配置回调”等价于全部授权。
        self.approve = approve or (lambda tool, args: not tool.needs_approval)
        self.conversation = Conversation(
            system_prompt=(
                system_prompt
                if system_prompt is not None
                else build_system_prompt(config.workspace, registry.names())
            )
        )

    # ------------------------------------------------------------ 主循环

    def run(self, task: str) -> RunResult:
        self.bus.emit(
            ev.RunStarted(task=task, model=self.config.model, cwd=str(self.config.workspace))
        )
        self.conversation.add_user(task)

        call_counts: dict[str, int] = {}
        tool_call_counts: dict[str, int] = {}
        state = _RunState()
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
                return self._finish(StopReason.ERROR, step, final_text, state)

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

            # 模型认为完成不等于内核确认完成。只读任务可以直接结束；发生过修改的
            # 任务必须在最后一次修改之后留下成功验证证据。
            if not reply.calls:
                self.conversation.add_assistant(reply.text, [])
                if self._reply_was_truncated(reply):
                    if (
                        state.continuation_nudges < MAX_CONTINUATION_NUDGES
                        and step < self.config.max_steps
                    ):
                        state.continuation_nudges += 1
                        self.conversation.add_user(TRUNCATED_CONTINUATION_REQUIRED)
                        self.bus.emit(
                            ev.ContinuationRequired(
                                attempt=state.continuation_nudges,
                                max_attempts=MAX_CONTINUATION_NUDGES,
                                finish_reason=reply.finish_reason or "token_limit_fallback",
                                message=TRUNCATED_CONTINUATION_REQUIRED,
                            )
                        )
                        continue
                    return self._finish(
                        StopReason.INCOMPLETE, step, final_text, state
                    )
                if not state.changed_paths:
                    return self._finish(
                        StopReason.DONE_READ_ONLY, step, final_text, state
                    )
                if state.verified:
                    return self._finish(
                        StopReason.DONE_VERIFIED, step, final_text, state
                    )
                if not state.verification_nudged and step < self.config.max_steps:
                    state.verification_nudged = True
                    self.conversation.add_user(VERIFICATION_REQUIRED)
                    self.bus.emit(
                        ev.VerificationRequired(
                            changed_paths=tuple(sorted(state.changed_paths)),
                            message=VERIFICATION_REQUIRED,
                        )
                    )
                    continue
                return self._finish(
                    StopReason.DONE_UNVERIFIED, step, final_text, state
                )

            self.conversation.add_assistant(reply.text, reply.calls)

            stop = self._execute_calls(
                reply.calls, call_counts, tool_call_counts, state, step
            )
            if stop is not None:
                return self._finish(stop, step, final_text, state)

        return self._finish(StopReason.MAX_STEPS, step, final_text, state)

    def _reply_was_truncated(self, reply: ModelReply) -> bool:
        reason = reply.finish_reason.strip().lower()
        if reason in {"length", "max_tokens"}:
            return True
        # 有些兼容端点不回 finish_reason；usage 已撞到请求上限时保守续跑一次。
        return (
            not reply.calls
            and reply.completion_tokens >= self.config.max_output_tokens
        )

    # ------------------------------------------------------------ 工具执行

    def _execute_calls(
        self,
        calls: list,
        call_counts: dict[str, int],
        tool_call_counts: dict[str, int],
        state: _RunState,
        step: int,
    ) -> StopReason | None:
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
            tool_call_counts[call.name] = tool_call_counts.get(call.name, 0) + 1

            # 无进展检测
            signature = f"{call.name}:{json.dumps(call.args, sort_keys=True, ensure_ascii=False)}"
            call_counts[signature] = call_counts.get(signature, 0) + 1
            repeats = call_counts[signature]
            if repeats >= STUCK_THRESHOLD:
                self.conversation.add_tool_result(
                    call.call_id, call.name, "检测到重复调用，已终止。"
                )
                return StopReason.STUCK

            # 用户拒绝是人的最终决定，不重试
            if tool is not None and tool.needs_approval and not self.approve(tool, call.args):
                self.conversation.add_tool_result(
                    call.call_id, call.name, "用户拒绝了该操作。"
                )
                return StopReason.DENIED

            self.bus.emit(
                ev.ToolCallStarted(call_id=call.call_id, name=call.name, args=call.args)
            )

            started = time.perf_counter()
            if (
                tool is not None
                and tool.max_calls_per_run > 0
                and tool_call_counts[call.name] > tool.max_calls_per_run
            ):
                result = self._tool_limit_result(tool)
            else:
                result = self.registry.invoke(call.name, call.args)
            duration_ms = int((time.perf_counter() - started) * 1000)

            # 委派工具的模型调用也属于本次任务成本，不能从总 token 中隐身。
            self.conversation.total_prompt_tokens += result.prompt_tokens
            self.conversation.total_completion_tokens += result.completion_tokens

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
                state.record_change(result.touched_paths)

            state.record_verification(
                ok=result.ok,
                kind=result.verification_kind,
                command=result.verification_command,
                summary=result.summary,
                step=step,
            )

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

    @staticmethod
    def _tool_limit_result(tool: Tool) -> ToolResult:
        return ToolResult(
            ok=False,
            detail=(
                f"{tool.name} 每个任务最多调用 {tool.max_calls_per_run} 次。"
                "请使用已有调查结果继续完成主任务。"
            ),
            summary=f"超过 {tool.name} 单任务调用上限",
        )

    # ------------------------------------------------------------ 收尾

    def _finish(
        self,
        reason: StopReason,
        steps: int,
        final_text: str,
        state: _RunState,
    ) -> RunResult:
        total = (
            self.conversation.total_prompt_tokens
            + self.conversation.total_completion_tokens
        )
        evidence = tuple(state.evidence)
        changed_paths = tuple(sorted(state.changed_paths))
        self.bus.emit(
            ev.RunFinished(
                reason=reason.value,
                steps=steps,
                total_tokens=total,
                changed_paths=changed_paths,
                verification=(evidence[-1].summary if evidence else ""),
            )
        )
        return RunResult(
            reason=reason,
            steps=steps,
            total_tokens=total,
            final_text=final_text,
            changed_paths=changed_paths,
            verification=evidence,
        )
