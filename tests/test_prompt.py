from pathlib import Path

from hako import prompt as prompt_module
from hako.prompt import SYSTEM_PROMPT, build_system_prompt


def test_prompt_requests_verifiable_progress_without_hidden_reasoning() -> None:
    assert "公开进度要简短且可核查" in SYSTEM_PROMPT
    assert "只陈述当前上下文或工具结果能支持的已确认事实" in SYSTEM_PROMPT
    assert "不要输出隐藏思维链" in SYSTEM_PROMPT


def test_windows_prompt_prefers_repository_test_entry(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "test.ps1").write_text("exit 0\n", encoding="utf-8")
    monkeypatch.setattr(prompt_module.sys, "platform", "win32")

    prompt = build_system_prompt(tmp_path, ["run_command"])

    assert ".\\test.ps1" in prompt
    assert "不要猜测 `.venv` 路径" in prompt
    assert "不要再临时拼接 `python -c`" in prompt
