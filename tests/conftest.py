"""测试夹具。

这里刻意不 mock 文件系统：工具的行为里有一半是操作系统的行为
（路径解析、符号链接、编码、换行），mock 掉就等于没测。用 tmp_path 真跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hako.config import Config  # noqa: E402
from hako.events import EventBus  # noqa: E402
from hako.tools import build_default_registry  # noqa: E402


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    # resolve()：macOS 上 tmp_path 是 /var/... 而 /var 是 /private/var 的符号链接，
    # 不 resolve 的话沙箱检查会把自己的工作目录判成越界。
    return tmp_path.resolve()


@pytest.fixture
def registry(workspace: Path):
    return build_default_registry(workspace)


@pytest.fixture
def config(workspace: Path) -> Config:
    return Config(
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="test-model",
        context_limit=8192,
        workspace=workspace,
        max_steps=8,
    )


@pytest.fixture
def bus() -> EventBus:
    return EventBus()
