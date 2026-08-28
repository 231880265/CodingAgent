"""受限只读 subagent：隔离调查上下文，主 Agent 保留唯一写权限。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from . import events as ev
from .config import Config
from .llm import LLMClient
from .loop import Agent, StopReason
from .tools import Tool, ToolResult, build_readonly_registry

READONLY_SYSTEM_PROMPT = """你是 hako 的只读调查 subagent。主 Agent 会给你一个边界明确的问题；你的职责是实际调用 list_dir/read_file 收集证据，再返回简洁调查备忘录。

硬边界：
- 你只能列目录和读文件，不能修改文件、运行命令、启动另一个 agent 或宣称测试已经通过。
- 结论必须指向具体文件、函数或日志行；区分观察事实与推断。
- 不要设计完整补丁，不要长篇纸上推演。证据足够后立即结束，按“观察 / 推断 / 建议主 Agent 下一步”组织，不超过 700 个中文字符。
"""


class _ContextPeak:
    def __init__(self) -> None:
        self.value = 0

    def handle(self, event: ev.Event) -> None:
        if isinstance(event, ev.ContextStats):
            self.value = max(self.value, event.used_tokens)


def _child_prompt(workspace: Path) -> str:
    return (
        f"{READONLY_SYSTEM_PROMPT}\n"
        f"## 当前环境\n\n"
        f"- 工作目录：{workspace}\n"
        "- 可用工具：read_file, list_dir\n"
    )


def make_delegate_readonly(
    config: Config,
    parent_bus: ev.EventBus,
    client_factory: Callable[[], LLMClient] | None = None,
) -> Tool:
    """创建一次/任务的只读委派工具；client_factory 仅用于离线测试。"""

    def default_client() -> LLMClient:
        return LLMClient(
            config.api_key,
            config.base_url,
            config.model,
            max_output_tokens=min(config.max_output_tokens, 2048),
            enable_thinking=config.enable_thinking,
        )

    make_client = client_factory or default_client

    def delegate_readonly(task: str) -> ToolResult:
        question = task.strip()
        if not question:
            return ToolResult(ok=False, detail="调查问题不能为空")
        if len(question) > 2000:
            return ToolResult(ok=False, detail="调查问题过长；请收窄到一个具体问题")

        child_config = replace(
            config,
            max_steps=max(1, config.subagent_max_steps),
            enable_subagent=False,
        )
        child_bus = ev.EventBus()
        peak = _ContextPeak()
        child_bus.subscribe(peak.handle)
        child = Agent(
            config=child_config,
            registry=build_readonly_registry(config.workspace),
            client=make_client(),
            bus=child_bus,
            system_prompt=_child_prompt(config.workspace),
        )

        parent_bus.emit(
            ev.SubagentStarted(task=question, max_steps=child_config.max_steps)
        )
        result = child.run(
            "主 Agent 委派的只读调查：\n"
            f"{question}\n\n"
            "请先使用工具取证，再提交调查备忘录。"
        )
        ok = result.reason is StopReason.DONE_READ_ONLY and bool(result.final_text.strip())
        parent_bus.emit(
            ev.SubagentFinished(
                ok=ok,
                reason=result.reason.value,
                steps=result.steps,
                total_tokens=result.total_tokens,
                max_context_tokens=peak.value,
            )
        )

        label = "只读调查完成" if ok else "只读调查未正常完成"
        memo = result.final_text.strip() or "subagent 没有返回可用备忘录。"
        return ToolResult(
            ok=ok,
            detail=(
                f"[{label}]\n{memo}\n\n"
                "[边界] 以上内容未经测试验证；主 Agent 必须自行核对、修改和验证。"
            ),
            summary=(
                f"{label} · {result.steps} 步 · {result.total_tokens:,} tokens"
            ),
            prompt_tokens=child.conversation.total_prompt_tokens,
            completion_tokens=child.conversation.total_completion_tokens,
        )

    return Tool(
        name="delegate_readonly",
        description=(
            "把一个跨多个文件或日志的具体调查问题委派给隔离的只读 subagent。"
            "它只能 list/read，不能写入或运行命令；每个主任务最多调用一次。"
            "返回的是证据备忘录，不是已验证结论，主 Agent 仍须自行修改和测试。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "一个边界明确的只读调查问题，说明要关联哪些症状或文件。",
                }
            },
            "required": ["task"],
        },
        handler=delegate_readonly,
        read_only=True,
        needs_approval=False,
        max_calls_per_run=1,
    )
