"""配置：凭据只从环境变量 / .env 读，绝不写进仓库。

自己解析 .env 而不引 python-dotenv：十几行的事，少一个依赖好辩护。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 各家 OpenAI 兼容端点。填了哪个 KEY 就用哪家，省掉一个必填配置项。
PROVIDERS = {
    "DEEPSEEK_API_KEY": ("https://api.deepseek.com/v1", "deepseek-chat", 65536),
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

    @classmethod
    def from_env(cls, workspace: Path | None = None) -> "Config":
        root = Path(__file__).resolve().parent.parent
        load_dotenv(root / ".env")

        api_key = base_url = model = ""
        context_limit = 65536
        for env_name, (url, default_model, limit) in PROVIDERS.items():
            value = os.environ.get(env_name, "").strip()
            if value:
                api_key, base_url, model, context_limit = value, url, default_model, limit
                break

        if not api_key:
            names = " / ".join(PROVIDERS)
            raise SystemExit(
                f"未找到 API key。请复制 .env.example 为 .env 并填入 {names} 之一。"
            )

        # 显式覆盖优先于自动推断
        base_url = os.environ.get("HAKO_BASE_URL", "").strip() or base_url
        model = os.environ.get("HAKO_MODEL", "").strip() or model

        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            context_limit=_int_env("HAKO_CONTEXT_LIMIT", context_limit),
            workspace=(workspace or Path.cwd()).resolve(),
            max_steps=_int_env("HAKO_MAX_STEPS", 40),
        )


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
