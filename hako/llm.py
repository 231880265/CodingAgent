"""模型客户端：请求、重试、以及模型输出的解析。

设计决策（见 DESIGN.md #5）：主路径走原生 tool calling，但**不信任**它的输出格式。
实测中 arguments 字段出现过：被 ```json 包裹、尾随逗号、字符串未闭合（流式截断）、
以及整个 JSON 是个字符串字面量（双重编码）。这些都不该让一整轮白费——
能修就修，修不了就把错误回传给模型重试。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from openai import APIError, APITimeoutError, OpenAI, RateLimitError


@dataclass
class ParsedCall:
    call_id: str
    name: str
    args: dict[str, Any]
    # 解析失败时非空：这条消息会作为错误回传给模型，而不是执行
    parse_error: str = ""


@dataclass
class ModelReply:
    text: str
    calls: list[ParsedCall]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # stop / tool_calls / length 等。主循环必须知道是否因输出上限截断；
    # “没有 tool_calls”本身不等于模型已经完成任务。
    finish_reason: str = ""
    raw_message: Any = None


# ---------------------------------------------------------------- JSON 修补

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_arguments(raw: str) -> tuple[dict[str, Any], str]:
    """尽力把 arguments 解析成 dict。返回 (args, error)，error 非空表示放弃。"""
    if raw is None or not str(raw).strip():
        return {}, ""                      # 无参工具，合法

    text = _FENCE.sub("", str(raw).strip())

    for attempt in (text, _strip_trailing_commas(text), _close_braces(text)):
        try:
            value = json.loads(attempt)
        except (json.JSONDecodeError, TypeError):
            continue
        # 双重编码：arguments 是一个 JSON 字符串，里面才是对象
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return {}, f"arguments 是字符串而非对象：{value[:200]}"
        if isinstance(value, dict):
            return value, ""
        return {}, f"arguments 需要是 JSON 对象，实际是 {type(value).__name__}"

    return {}, f"arguments 不是合法 JSON：{text[:200]}"


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _close_braces(text: str) -> str:
    """流式截断的常见形态：括号没闭合。补齐后再试一次。"""
    if text.count('"') % 2 == 1:
        text += '"'
    text += "]" * max(0, text.count("[") - text.count("]"))
    text += "}" * max(0, text.count("{") - text.count("}"))
    return text


# ---------------------------------------------------------------- 客户端


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_output_tokens: int = 4096,
        enable_thinking: bool | None = None,
    ) -> None:
        self.model = model
        self.max_output_tokens = max(1, int(max_output_tokens))
        self.enable_thinking = enable_thinking
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0, max_retries=0)

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply:
        """一次请求。网络类错误自己退避重试，业务类错误直接抛。"""
        last_error: Exception | None = None

        for attempt in range(4):
            try:
                request: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "tools": tools,
                    "temperature": 0.0,
                    "max_tokens": self.max_output_tokens,
                }
                if self.enable_thinking is not None:
                    request["extra_body"] = {"enable_thinking": self.enable_thinking}

                response = self._client.chat.completions.create(
                    **request,
                )
                return self._normalize(response)
            except (RateLimitError, APITimeoutError) as exc:
                last_error = exc
                time.sleep(min(2**attempt, 8))          # 1s, 2s, 4s, 8s
            except APIError as exc:
                # 4xx 里除了 429 都是我们自己的问题（key 错、模型名错、消息结构非法），
                # 重试只会重复失败。直接抛给上层作为不可恢复错误。
                status = getattr(exc, "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
                last_error = exc
                time.sleep(min(2**attempt, 8))

        raise RuntimeError(f"模型请求连续 4 次失败：{last_error}") from last_error

    def _normalize(self, response: Any) -> ModelReply:
        choice = response.choices[0]
        message = choice.message
        usage = getattr(response, "usage", None)

        calls: list[ParsedCall] = []
        for index, call in enumerate(getattr(message, "tool_calls", None) or []):
            args, error = parse_arguments(call.function.arguments)
            calls.append(
                ParsedCall(
                    # 有的兼容端点不回 id，自己补一个，否则 tool 消息无法配对
                    call_id=call.id or f"call_{index}",
                    name=call.function.name or "",
                    args=args,
                    parse_error=error,
                )
            )

        return ModelReply(
            text=(message.content or "").strip(),
            calls=calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            finish_reason=str(getattr(choice, "finish_reason", "") or ""),
            raw_message=message,
        )


def estimate_tokens(text: str) -> int:
    """粗估 token 数。

    不引 tiktoken：它是 OpenAI 的分词器，而我们跑在 DeepSeek/Qwen 上——
    用它会得到**看起来精确其实是错的**数字。真实占用一律取 API 返回的 usage，
    这里只用于发请求前的预算判断，够粗即可。
    中文约 1 token/字，英文约 1 token/4 字符。
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return cjk + (len(text) - cjk) // 4 + 1
