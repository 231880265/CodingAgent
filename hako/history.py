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
    tool_path: str = ""          # read_file 读的是哪个文件
    stale: bool = False


PLACEHOLDER = (
    "[该文件在此之后已被修改，原读取结果已失效并移除。"
    "如需当前内容请重新调用 read_file。]"
)


@dataclass
class Conversation:
    system_prompt: str
    turns: list[Turn] = field(default_factory=list)
    # 累计真实用量（取 API 的 usage，非估算）
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0

    # ------------------------------------------------------------ 追加

    def add_user(self, text: str) -> None:
        self.turns.append(Turn({"role": "user", "content": text}))

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
        self, call_id: str, name: str, detail: str, path: str = ""
    ) -> None:
        # 每个 tool_call 都必须有配对的 tool 消息，否则下一轮请求会被 API 拒绝。
        # 即使解析失败或用户拒绝执行，也要回一条——这是硬约束，不是可选项。
        self.turns.append(
            Turn(
                {"role": "tool", "tool_call_id": call_id, "content": detail},
                tool_name=name,
                tool_path=path,
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
        return [{"role": "system", "content": self.system_prompt}] + [
            turn.message for turn in self.turns
        ]

    def estimated_tokens(self) -> int:
        """发请求前的粗估占用，用于判断是否该压缩。"""
        total = estimate_tokens(self.system_prompt)
        for turn in self.turns:
            content = turn.message.get("content") or ""
            total += estimate_tokens(str(content))
            for call in turn.message.get("tool_calls", []):
                total += estimate_tokens(json.dumps(call["function"], ensure_ascii=False))
        return total
