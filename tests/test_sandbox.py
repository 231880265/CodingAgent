"""路径沙箱。

这是唯一一个"失败就是安全事故"的模块，所以测得比别处细。
每个 case 都是一种真实的逃逸手法，不是凑覆盖率。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hako.tools.base import ToolError, rel, resolve_in_workspace


def test_plain_relative_path_ok(workspace: Path):
    target = resolve_in_workspace(workspace, "src/main.py")
    assert target == workspace / "src" / "main.py"


def test_dot_prefix_normalized(workspace: Path):
    """模型很爱写 ./ 前缀。规范化后必须和不带前缀的结果一致——
    否则陈旧读取失效会因为字符串不等而静默失灵。"""
    assert resolve_in_workspace(workspace, "./src/a.py") == resolve_in_workspace(
        workspace, "src/a.py"
    )


def test_dot_prefix_yields_same_rel(workspace: Path):
    a = resolve_in_workspace(workspace, "./src/a.py")
    b = resolve_in_workspace(workspace, "src/a.py")
    assert rel(workspace, a) == rel(workspace, b) == "src/a.py"


@pytest.mark.parametrize(
    "escape",
    [
        "../secrets.txt",
        "../../etc/passwd",
        "src/../../outside.py",
        "./../outside.py",
    ],
)
def test_parent_traversal_rejected(workspace: Path, escape: str):
    with pytest.raises(ToolError, match="工作目录之外"):
        resolve_in_workspace(workspace, escape)


def test_absolute_path_outside_rejected(workspace: Path):
    outside = "C:\\Windows\\System32\\drivers\\etc\\hosts" if sys.platform == "win32" else "/etc/passwd"
    with pytest.raises(ToolError, match="工作目录之外"):
        resolve_in_workspace(workspace, outside)


def test_absolute_path_inside_allowed(workspace: Path):
    """绝对路径本身不是问题，越界才是问题。"""
    inside = str(workspace / "notes.md")
    assert resolve_in_workspace(workspace, inside) == workspace / "notes.md"


def test_empty_path_rejected(workspace: Path):
    for bad in ("", "   "):
        with pytest.raises(ToolError, match="不能为空"):
            resolve_in_workspace(workspace, bad)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 建符号链接需要管理员权限")
def test_symlink_escape_rejected(workspace: Path, tmp_path: Path):
    """先 resolve 再比前缀的真正理由：只挡 '..' 字符串挡不住这个。"""
    secret = tmp_path.parent / "outside_secret.txt"
    secret.write_text("token", encoding="utf-8")
    (workspace / "link.txt").symlink_to(secret)

    with pytest.raises(ToolError, match="工作目录之外"):
        resolve_in_workspace(workspace, "link.txt")


def test_rel_falls_back_to_absolute(workspace: Path, tmp_path: Path):
    """rel 拿到不在工作目录下的路径时不应崩，退回绝对路径显示。"""
    other = (tmp_path.parent / "elsewhere.py").resolve()
    assert rel(workspace, other) == other.as_posix()
