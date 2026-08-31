from hako.prompt import SYSTEM_PROMPT


def test_prompt_requests_verifiable_progress_without_hidden_reasoning() -> None:
    assert "公开进度要简短且可核查" in SYSTEM_PROMPT
    assert "只陈述当前上下文或工具结果能支持的已确认事实" in SYSTEM_PROMPT
    assert "不要输出隐藏思维链" in SYSTEM_PROMPT
