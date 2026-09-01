"""配置：凭据只从环境变量 / .env 读，绝不写进仓库。

自己解析 .env 而不引 python-dotenv：十几行的事，少一个依赖好辩护。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 各家 OpenAI 兼容端点。旧的提供商专用 Key 仍然保留，避免已有配置失效；
# 新接入统一优先使用 HAKO_API_KEY + HAKO_BASE_URL + HAKO_MODEL。
PROVIDERS = {
    "SILICONFLOW_API_KEY": (
        "https://api.siliconflow.cn/v1",
        "deepseek-ai/DeepSeek-V4-Flash",
        1_000_000,
    ),
    "DEEPSEEK_API_KEY": (
        "https://api.deepseek.com/v1",
        "deepseek-v4-flash",
        1_000_000,
    ),
    "DASHSCOPE_API_KEY": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        131072,
    ),
    "ZHIPU_API_KEY": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-plus", 131072),
}


def load_dotenv(path: Path) -> None:
    """把 .env 里的键值塞进 os.environ。已存在的环境变量优先，不覆盖。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


@dataclass
class Config:
    api_key: str
    base_url: str
    model: str
    context_limit: int
    workspace: Path
    max_steps: int = 40
    # 单个工具结果回传给模型的上限（字符数）。见 truncate.py
    tool_result_budget: int = 6000
    # 限制单轮回复，避免推理模型在简单工具决策上生成过长内容。
    max_output_tokens: int = 4096
    # None 表示不向提供商发送该扩展字段；硅基流动默认关闭长思考以降低交互延迟。
    enable_thinking: bool | None = None
    # 默认关闭，便于对照；开启后主 Agent 多一个受限的只读调查工具。
    enable_subagent: bool = False
    subagent_max_steps: int = 6
    # Repository Experience Memory：跨 Session 检索同一工作区的工程事实。
    repository_memory_enabled: bool = True
    memory_embedding_provider: str = "hash"
    memory_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    memory_top_k: int = 6
    memory_dense_backup_k: int = 4
    memory_rerank_top_k: int = 4
    memory_relevance_weight: float = 0.7
    memory_importance_weight: float = 0.2
    memory_recency_weight: float = 0.1
    memory_recency_lambda: float = 0.01
    memory_rerank_enabled: bool = False
    memory_rerank_model: str = ""
    memory_rerank_timeout_seconds: float = 20.0
    # 单 Run Working Memory 压缩。默认关闭，确保现有短任务行为完全不变；
    # 开启后只改变下一次模型请求的临时 Conversation View，不改审计历史。
    compaction_enabled: bool = False
    compaction_threshold: float = 0.70
    compaction_keep_recent_messages: int = 12
    compaction_model: str = ""
    compaction_timeout_seconds: float = 20.0

    @classmethod
    def from_env(cls, workspace: Path | None = None) -> "Config":
        root = Path(__file__).resolve().parent.parent
        load_dotenv(root / ".env")

        api_key = os.environ.get("HAKO_API_KEY", "").strip()
        base_url = model = ""
        context_limit = 65536

        if api_key:
            base_url = os.environ.get("HAKO_BASE_URL", "").strip()
            model = os.environ.get("HAKO_MODEL", "").strip()
            missing = [
                name
                for name, value in (
                    ("HAKO_BASE_URL", base_url),
                    ("HAKO_MODEL", model),
                )
                if not value
            ]
            if missing:
                raise SystemExit(
                    "使用 HAKO_API_KEY 时还必须填写 " + "、".join(missing) + "。"
                )
        else:
            for env_name, (url, default_model, limit) in PROVIDERS.items():
                value = os.environ.get(env_name, "").strip()
                if value:
                    api_key, base_url, model, context_limit = (
                        value,
                        url,
                        default_model,
                        limit,
                    )
                    break

        if not api_key:
            names = " / ".join(PROVIDERS)
            raise SystemExit(
                "未找到 API key。请复制 .env.example 为 .env 并填写 "
                f"HAKO_API_KEY，或填入 {names} 之一。"
            )

        # 旧提供商专用 Key 仍允许用 HAKO_* 显式覆盖自动推断结果。
        base_url = os.environ.get("HAKO_BASE_URL", "").strip() or base_url
        model = os.environ.get("HAKO_MODEL", "").strip() or model

        thinking_raw = os.environ.get("HAKO_ENABLE_THINKING", "").strip()
        if thinking_raw:
            enable_thinking = _bool_value(thinking_raw, "HAKO_ENABLE_THINKING")
        elif "siliconflow.cn" in base_url.lower():
            enable_thinking = False
        else:
            enable_thinking = None

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            context_limit=_int_env("HAKO_CONTEXT_LIMIT", context_limit),
            workspace=(workspace or Path.cwd()).resolve(),
            max_steps=_int_env("HAKO_MAX_STEPS", 40),
            max_output_tokens=_int_env("HAKO_MAX_OUTPUT_TOKENS", 4096),
            enable_thinking=enable_thinking,
            enable_subagent=_bool_env("HAKO_ENABLE_SUBAGENT", False),
            subagent_max_steps=_int_env("HAKO_SUBAGENT_MAX_STEPS", 6),
            repository_memory_enabled=_bool_env("HAKO_REPOSITORY_MEMORY_ENABLED", True),
            memory_embedding_provider=(
                os.environ.get("HAKO_MEMORY_EMBEDDING_PROVIDER", "").strip().lower()
                or "hash"
            ),
            memory_embedding_model=(
                os.environ.get("HAKO_MEMORY_EMBEDDING_MODEL", "").strip()
                or "BAAI/bge-small-zh-v1.5"
            ),
            memory_top_k=_int_env("HAKO_MEMORY_TOP_K", 6),
            memory_dense_backup_k=_int_env("HAKO_MEMORY_DENSE_BACKUP_K", 4),
            memory_rerank_top_k=_int_env("HAKO_MEMORY_RERANK_TOP_K", 4),
            memory_relevance_weight=_float_env("HAKO_MEMORY_RELEVANCE_WEIGHT", 0.7),
            memory_importance_weight=_float_env("HAKO_MEMORY_IMPORTANCE_WEIGHT", 0.2),
            memory_recency_weight=_float_env("HAKO_MEMORY_RECENCY_WEIGHT", 0.1),
            memory_recency_lambda=_float_env("HAKO_MEMORY_RECENCY_LAMBDA", 0.01),
            memory_rerank_enabled=_bool_env("HAKO_MEMORY_RERANK_ENABLED", False),
            memory_rerank_model=os.environ.get("HAKO_MEMORY_RERANK_MODEL", "").strip(),
            memory_rerank_timeout_seconds=_float_env(
                "HAKO_MEMORY_RERANK_TIMEOUT_SECONDS", 20.0
            ),
            compaction_enabled=_bool_env("HAKO_COMPACTION_ENABLED", False),
            compaction_threshold=_bounded_float_env(
                "HAKO_COMPACTION_THRESHOLD", 0.70, minimum=0.05, maximum=0.95
            ),
            compaction_keep_recent_messages=max(
                2, _int_env("HAKO_COMPACTION_KEEP_RECENT_MESSAGES", 12)
            ),
            compaction_model=os.environ.get("HAKO_COMPACTION_MODEL", "").strip(),
            compaction_timeout_seconds=max(
                1.0, _float_env("HAKO_COMPACTION_TIMEOUT_SECONDS", 20.0)
            ),
        )


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _bounded_float_env(
    name: str, default: float, *, minimum: float, maximum: float
) -> float:
    value = _float_env(name, default)
    return min(maximum, max(minimum, value))


def _bool_value(raw: str, name: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(
        f"{name} 只能填写 true/false、1/0、yes/no 或 on/off。"
    )


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip()
    return _bool_value(raw, name) if raw else default
