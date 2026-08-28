"""LLM 请求参数：提供商扩展必须显式、可测试。"""

from __future__ import annotations

from types import SimpleNamespace

from hako.llm import LLMClient


class CapturingCompletions:
    def __init__(self, finish_reason: str = "stop") -> None:
        self.request = None
        self.finish_reason = finish_reason

    def create(self, **kwargs):
        self.request = kwargs
        message = SimpleNamespace(content="ok", tool_calls=[])
        choice = SimpleNamespace(message=message, finish_reason=self.finish_reason)
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        return SimpleNamespace(choices=[choice], usage=usage)


def _client(enable_thinking, finish_reason: str = "stop"):
    client = LLMClient(
        "test-key",
        "https://example.invalid/v1",
        "test-model",
        max_output_tokens=512,
        enable_thinking=enable_thinking,
    )
    completions = CapturingCompletions(finish_reason)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_complete_sends_output_limit_and_thinking_mode():
    client, completions = _client(False)

    client.complete([{"role": "user", "content": "hi"}], [])

    assert completions.request["max_tokens"] == 512
    assert completions.request["extra_body"] == {"enable_thinking": False}


def test_complete_omits_provider_extension_when_unspecified():
    client, completions = _client(None)

    client.complete([{"role": "user", "content": "hi"}], [])

    assert "extra_body" not in completions.request


def test_complete_preserves_finish_reason_for_loop_control():
    client, _ = _client(False, finish_reason="length")

    result = client.complete([{"role": "user", "content": "hi"}], [])

    assert result.finish_reason == "length"
