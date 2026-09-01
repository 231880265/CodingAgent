"""配置解析：提供商默认值必须可预测，密钥只留在环境变量中。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hako.config import Config, PROVIDERS


def _clear_provider_keys(monkeypatch) -> None:
    # 设为空字符串而不是删除，防止开发机上的 .env 在测试中重新注入真实密钥。
    monkeypatch.setenv("HAKO_API_KEY", "")
    for name in PROVIDERS:
        monkeypatch.setenv(name, "")


def test_generic_api_configuration_takes_priority(monkeypatch, tmp_path: Path):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("HAKO_API_KEY", "generic-test-key")
    monkeypatch.setenv("HAKO_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("HAKO_MODEL", "gpt-compatible-model")
    monkeypatch.setenv("HAKO_CONTEXT_LIMIT", "262144")
    # 同时存在旧 Key 时也不产生含糊选择：通用配置明确优先。
    monkeypatch.setenv("SILICONFLOW_API_KEY", "legacy-test-key")

    config = Config.from_env(workspace=tmp_path)

    assert config.api_key == "generic-test-key"
    assert config.base_url == "https://gateway.example/v1"
    assert config.model == "gpt-compatible-model"
    assert config.context_limit == 262144


@pytest.mark.parametrize("missing", ["HAKO_BASE_URL", "HAKO_MODEL"])
def test_generic_api_configuration_requires_endpoint_and_model(
    monkeypatch, tmp_path: Path, missing: str
):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("HAKO_API_KEY", "generic-test-key")
    monkeypatch.setenv("HAKO_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("HAKO_MODEL", "gpt-compatible-model")
    monkeypatch.setenv(missing, "")

    with pytest.raises(SystemExit, match=missing):
        Config.from_env(workspace=tmp_path)


def test_siliconflow_defaults_to_deepseek_v4_flash(monkeypatch, tmp_path: Path):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    # 空值会阻止开发机 .env 的 Claude/LMU 配置重新注入测试进程，确保这里
    # 真正验证旧的硅基流动专用 Key 默认值，而不是依赖测试机当前配置。
    monkeypatch.setenv("HAKO_BASE_URL", "")
    monkeypatch.setenv("HAKO_MODEL", "")
    monkeypatch.setenv("HAKO_CONTEXT_LIMIT", "")

    config = Config.from_env(workspace=tmp_path)

    assert config.api_key == "test-key"
    assert config.base_url == "https://api.siliconflow.cn/v1"
    assert config.model == "deepseek-ai/DeepSeek-V4-Flash"
    assert config.context_limit == 1_000_000
    assert config.max_output_tokens == 4096
    assert config.enable_thinking is False


def test_deepseek_defaults_to_current_v4_flash_name(monkeypatch, tmp_path: Path):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("HAKO_BASE_URL", "")
    monkeypatch.setenv("HAKO_MODEL", "")
    monkeypatch.setenv("HAKO_CONTEXT_LIMIT", "")

    config = Config.from_env(workspace=tmp_path)

    assert config.base_url == "https://api.deepseek.com/v1"
    assert config.model == "deepseek-v4-flash"
    assert config.context_limit == 1_000_000


def test_explicit_model_and_endpoint_override_provider_defaults(monkeypatch, tmp_path: Path):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-key")
    monkeypatch.setenv("HAKO_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("HAKO_MODEL", "example/model")
    monkeypatch.setenv("HAKO_CONTEXT_LIMIT", "12345")
    monkeypatch.setenv("HAKO_MAX_OUTPUT_TOKENS", "512")
    monkeypatch.setenv("HAKO_ENABLE_THINKING", "true")
    monkeypatch.setenv("HAKO_ENABLE_SUBAGENT", "true")
    monkeypatch.setenv("HAKO_SUBAGENT_MAX_STEPS", "4")
    monkeypatch.setenv("HAKO_REPOSITORY_MEMORY_ENABLED", "false")
    monkeypatch.setenv("HAKO_MEMORY_EMBEDDING_PROVIDER", "sentence_transformer")
    monkeypatch.setenv("HAKO_MEMORY_TOP_K", "9")
    monkeypatch.setenv("HAKO_MEMORY_RELEVANCE_WEIGHT", "0.6")
    monkeypatch.setenv("HAKO_MEMORY_RERANK_ENABLED", "true")
    monkeypatch.setenv("HAKO_MEMORY_RERANK_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("HAKO_COMPACTION_ENABLED", "true")
    monkeypatch.setenv("HAKO_COMPACTION_THRESHOLD", "0.82")
    monkeypatch.setenv("HAKO_COMPACTION_KEEP_RECENT_MESSAGES", "16")
    monkeypatch.setenv("HAKO_COMPACTION_MODEL", "summary-model")
    monkeypatch.setenv("HAKO_COMPACTION_TIMEOUT_SECONDS", "9.5")

    config = Config.from_env(workspace=tmp_path)

    assert config.base_url == "https://example.invalid/v1"
    assert config.model == "example/model"
    assert config.context_limit == 12345
    assert config.max_output_tokens == 512
    assert config.enable_thinking is True
    assert config.enable_subagent is True
    assert config.subagent_max_steps == 4
    assert config.repository_memory_enabled is False
    assert config.memory_embedding_provider == "sentence_transformer"
    assert config.memory_top_k == 9
    assert config.memory_relevance_weight == pytest.approx(0.6)
    assert config.memory_rerank_enabled is True
    assert config.memory_rerank_timeout_seconds == pytest.approx(12.5)
    assert config.compaction_enabled is True
    assert config.compaction_threshold == pytest.approx(0.82)
    assert config.compaction_keep_recent_messages == 16
    assert config.compaction_model == "summary-model"
    assert config.compaction_timeout_seconds == pytest.approx(9.5)
