"""脚本化的假模型。

不 mock openai 的 SDK，而是替换掉整个 LLMClient：循环该关心的只有
"给一串消息、回一个 ModelReply"这个契约。这样测试跑得快、离线、可复现，
而且不会因为 SDK 换版本而碎掉。
"""

from __future__ import annotations

from typing import Any, Callable

from hako.llm import ModelReply, ParsedCall


def call(name: str, args: dict[str, Any], call_id: str = "", error: str = "") -> ParsedCall:
    return ParsedCall(
        call_id=call_id or f"c_{name}_{abs(hash(str(args))) % 10000}",
        name=name,
        args=args,
        parse_error=error,
    )


def reply(text: str = "", calls: list[ParsedCall] | None = None, **usage) -> ModelReply:
    return ModelReply(
        text=text,
        calls=calls or [],
        prompt_tokens=usage.get("prompt_tokens", 100),
        completion_tokens=usage.get("completion_tokens", 20),
        finish_reason=usage.get("finish_reason", "stop"),
    )


class FakeClient:
    """按脚本依次返回预设回复。

    脚本项可以是 ModelReply，也可以是接收当前消息列表的函数——
    后者用于断言"模型确实看到了失效后的上下文"。
    """

    def __init__(self, script: list[Any], model: str = "fake") -> None:
        self.script = list(script)
        self.model = model
        self.seen: list[list[dict[str, Any]]] = []
        self.calls_made = 0

    def complete(self, messages, tools) -> ModelReply:
        self.calls_made += 1
        # 存快照而不是引用：循环会继续改 conversation，存引用就看不出当时的样子
        self.seen.append([dict(m) for m in messages])

        if not self.script:
            return reply("脚本已耗尽")
        item = self.script.pop(0)
        return item(messages) if isinstance(item, Callable) else item


class ExplodingClient:
    """第 n 次调用抛异常，用于验证不可恢复错误的处理。"""

    def __init__(self, exc: Exception, fail_on: int = 1) -> None:
        self.exc = exc
        self.fail_on = fail_on
        self.calls_made = 0
        self.model = "fake"

    def complete(self, messages, tools) -> ModelReply:
        self.calls_made += 1
        if self.calls_made >= self.fail_on:
            raise self.exc
        return reply("ok")
