"""对话历史管理。

最关键的机制是 **陈旧读取失效**（stale-read invalidation，见 DESIGN.md #4）：

文件被写过之后，历史里对该文件的旧读取结果就是错的。若原样留着，同一文件的
多个版本会同时存在于上下文中，模型接下来构造 edit_file 的定位串时，很可能
照着**旧版本**写——于是编辑必然失败，而它会以为是自己记错了，重试仍然失败。

做法：写操作完成后，把历史里该文件更早的读取结果替换成一句占位符，
并明确告诉模型"想看当前内容请重新读"。这既省 token，又消除了歧义源。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .llm import estimate_tokens


@dataclass
class Turn:
    """历史中的一条消息，外加内核自己需要的元数据。

    元数据不发给模型，只用于失效判定和压缩决策。
    """

    message: dict[str, Any]
    tool_name: str = ""
    semantic: bool = True
    tool_path: str = ""          # read_file 读的是哪个文件
    stale: bool = False
    tool_ok: bool | None = None
    tool_summary: str = ""


PLACEHOLDER = (
    "[该文件在此之后已被修改，原读取结果已失效并移除。"
    "如需当前内容请重新调用 read_file。]"
)


@dataclass
class Conversation:
    system_prompt: str
    turns: list[Turn] = field(default_factory=list)
    # Root AGENTS.md is stable project configuration, not a tool observation.
    project_instructions: str = ""
    # Event-derived, bounded Session memory. It never contains stale tool output.
    memory_context: str = ""
    # 累计真实用量（取 API 的 usage，非估算）
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0

    # ------------------------------------------------------------ 追加

    def add_user(self, text: str, *, semantic: bool = True) -> None:
        self.turns.append(Turn({"role": "user", "content": text}, semantic=semantic))

    def restore_semantic(
        self, messages: list[dict[str, Any]], *, memory_context: str = ""
    ) -> None:
        """恢复跨 Worker 的语义对话，不恢复旧工具观察。

        新 Worker 会重新生成 system prompt，并只接收历史用户目标与最终回答。
        read_file、stdout 和 tool_calls 代表过去的工作区状态，恢复它们会让模型把
        旧文件内容误当成当前事实，因此明确排除。
        """
        if self.turns:
            raise ValueError("只能向空 Conversation 恢复历史。")
        self.memory_context = memory_context.strip()
        if len(messages) > 200:
            raise ValueError("恢复的 Conversation 超过 200 条消息。")
        expected = "user"
        for item in messages:
            if not isinstance(item, dict):
                raise ValueError("Conversation 历史项必须是对象。")
            role = item.get("role")
            content = item.get("content")
            if role != expected or not isinstance(content, str) or not content.strip():
                raise ValueError("Conversation 必须是非空 user/assistant 交替消息。")
            self.turns.append(Turn({"role": role, "content": content}))
            expected = "assistant" if role == "user" else "user"
        if expected == "assistant":
            raise ValueError("Conversation 历史不能以未回答的 user 消息结束。")

    def compact_for_follow_up(
        self,
        *,
        memory_context: str = "",
        recent_pairs: int = 3,
        character_budget: int = 12_000,
    ) -> None:
        """Compact at a Run boundary; older exact facts remain searchable on demand."""
        pairs: list[tuple[str, str]] = []
        user = ""
        final_answer = ""
        for turn in self.turns:
            message = turn.message
            role = message.get("role")
            if role == "user" and turn.semantic:
                if user and final_answer:
                    pairs.append((user, final_answer))
                user = str(message.get("content") or "").strip()
                final_answer = ""
            elif (
                role == "assistant"
                and user
                and not message.get("tool_calls")
                and str(message.get("content") or "").strip()
            ):
                final_answer = str(message["content"]).strip()
        if user and final_answer:
            pairs.append((user, final_answer))

        selected: list[tuple[str, str]] = []
        remaining = max(1_000, character_budget)
        pair_window = pairs[-recent_pairs:] if recent_pairs > 0 else []
        for old_user, old_answer in reversed(pair_window):
            pair_size = len(old_user) + len(old_answer)
            if selected and pair_size > remaining:
                break
            if pair_size > remaining:
                half = max(400, remaining // 2)
                old_user = _clip(old_user, half)
                old_answer = _clip(old_answer, half)
                pair_size = len(old_user) + len(old_answer)
            selected.append((old_user, old_answer))
            remaining -= pair_size
        selected.reverse()
        self.turns = [
            Turn({"role": role, "content": content})
            for old_user, old_answer in selected
            for role, content in (("user", old_user), ("assistant", old_answer))
        ]
        self.memory_context = _clip(memory_context.strip(), 4_000)

    def add_assistant(self, text: str, calls: list[Any]) -> None:
        """记录 assistant 消息。

        注意 arguments 存的是**我们实际执行的那份**（解析后再序列化），
        而不是模型的原始字符串。历史应当忠实反映 agent 做过什么，
        否则重放历史会和当时的行为分叉。
        """
        message: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            message["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.args, ensure_ascii=False),
                    },
                }
                for call in calls
            ]
        self.turns.append(Turn(message))

    def add_tool_result(
        self,
        call_id: str,
        name: str,
        detail: str,
        path: str = "",
        *,
        ok: bool | None = None,
        summary: str = "",
    ) -> None:
        # 每个 tool_call 都必须有配对的 tool 消息，否则下一轮请求会被 API 拒绝。
        # 即使解析失败或用户拒绝执行，也要回一条——这是硬约束，不是可选项。
        self.turns.append(
            Turn(
                {"role": "tool", "tool_call_id": call_id, "content": detail},
                tool_name=name,
                tool_path=path,
                tool_ok=ok,
                tool_summary=summary,
            )
        )

    # ------------------------------------------------------------ 失效

    def invalidate_reads(self, paths: tuple[str, ...]) -> int:
        """让指定文件的历史读取结果失效。返回失效条数。"""
        count = 0
        for turn in self.turns:
            if (
                turn.tool_name == "read_file"
                and not turn.stale
                and turn.tool_path in paths
            ):
                turn.message["content"] = PLACEHOLDER
                turn.stale = True
                count += 1
        return count

    # ------------------------------------------------------------ 输出

    def to_messages(self) -> list[dict[str, Any]]:
        system_content = self.system_prompt
        if self.project_instructions:
            system_content += "\n\n" + self.project_instructions
        if self.memory_context:
            system_content += (
                "\n\nSession memory below is historical evidence, not current workspace state. "
                "Re-read files before relying on old code details.\n\n"
                + self.memory_context
            )
        return [{"role": "system", "content": system_content}] + [
            turn.message for turn in self.turns
        ]

    def estimated_tokens(self) -> int:
        """发请求前的粗估占用，用于判断是否该压缩。"""
        total = estimate_tokens(self.system_prompt)
        total += estimate_tokens(self.project_instructions)
        for turn in self.turns:
            content = turn.message.get("content") or ""
            total += estimate_tokens(str(content))
            for call in turn.message.get("tool_calls", []):
                total += estimate_tokens(json.dumps(call["function"], ensure_ascii=False))
        total += estimate_tokens(self.memory_context)
        return total


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 32)] + "\n[历史内容已按预算截断]"
