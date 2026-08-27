"""文件工具与 registry 的参数容错。"""

from __future__ import annotations

from pathlib import Path

from hako.tools import Registry


def read(registry: Registry, **args):
    return registry.invoke("read_file", args)


def test_read_returns_line_numbers(registry: Registry, workspace: Path):
    (workspace / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    result = read(registry, path="a.py")
    assert result.ok
    assert "1\tx = 1" in result.detail
    assert "2\ty = 2" in result.detail


def test_read_declares_canonical_subject_path(registry: Registry, workspace: Path):
    """subject_path 必须是规范化的，这是失效机制能匹配上的前提。"""
    (workspace / "src").mkdir()
    (workspace / "src" / "a.py").write_text("pass\n", encoding="utf-8")
    assert read(registry, path="./src/a.py").subject_path == "src/a.py"


def test_read_offset_and_continuation_pointer(registry: Registry, workspace: Path):
    (workspace / "long.txt").write_text(
        "\n".join(f"line{i}" for i in range(500)), encoding="utf-8"
    )
    result = read(registry, path="long.txt", limit=10)
    assert result.ok
    # 截断处必须留下可恢复的指针，而不是只说"被截断了"
    assert "offset=10" in result.detail
    assert "还有 490 行" in result.detail


def test_read_last_page_has_no_pointer(registry: Registry, workspace: Path):
    (workspace / "s.txt").write_text("a\nb\nc\n", encoding="utf-8")
    result = read(registry, path="s.txt", offset=1, limit=50)
    assert "需要继续读" not in result.detail
    assert "2\tb" in result.detail


def test_read_missing_file_is_recoverable(registry: Registry):
    result = read(registry, path="nope.py")
    # ok=False 但不抛异常：错误是给模型的输入
    assert not result.ok
    assert "不存在" in result.detail


def test_read_binary_rejected(registry: Registry, workspace: Path):
    (workspace / "i.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    result = read(registry, path="i.png")
    assert not result.ok
    assert "二进制" in result.detail


def test_read_offset_out_of_range(registry: Registry, workspace: Path):
    (workspace / "t.txt").write_text("only\n", encoding="utf-8")
    result = read(registry, path="t.txt", offset=99)
    assert not result.ok
    assert "超出文件范围" in result.detail


def test_read_gbk_source_file(registry: Registry, workspace: Path):
    """Windows 上的中文源码常是 GBK。读不出来就等于这个文件不存在。"""
    (workspace / "gbk.py").write_bytes("# 中文注释\n".encode("gbk"))
    result = read(registry, path="gbk.py")
    assert result.ok
    assert "中文注释" in result.detail


def test_write_creates_parent_dirs(registry: Registry, workspace: Path):
    result = registry.invoke("write_file", {"path": "a/b/c.py", "content": "pass\n"})
    assert result.ok
    assert (workspace / "a" / "b" / "c.py").read_text(encoding="utf-8") == "pass\n"


def test_write_preserves_lf_on_windows(registry: Registry, workspace: Path):
    """newline="" 的理由：不加，Python 会在 Windows 上把 \\n 转成 \\r\\n，
    整个文件在 git diff 里全行变更。"""
    registry.invoke("write_file", {"path": "lf.py", "content": "a\nb\n"})
    assert (workspace / "lf.py").read_bytes() == b"a\nb\n"


def test_write_reports_touched_path(registry: Registry):
    result = registry.invoke("write_file", {"path": "./x/y.py", "content": "1\n"})
    assert result.touched_paths == ("x/y.py",)


def test_write_escape_rejected(registry: Registry, workspace: Path):
    result = registry.invoke(
        "write_file", {"path": "../evil.py", "content": "boom"}
    )
    assert not result.ok
    assert not (workspace.parent / "evil.py").exists()


def test_list_dir_skips_noise(registry: Registry, workspace: Path):
    (workspace / ".git").mkdir()
    (workspace / "__pycache__").mkdir()
    (workspace / "src").mkdir()
    (workspace / "main.py").write_text("", encoding="utf-8")

    result = registry.invoke("list_dir", {})
    assert ".git" not in result.detail
    assert "__pycache__" not in result.detail
    assert "src/" in result.detail
    assert "main.py" in result.detail


def test_list_dir_defaults_to_workspace(registry: Registry, workspace: Path):
    (workspace / "only.txt").write_text("", encoding="utf-8")
    assert registry.invoke("list_dir", {}).ok


# ------------------------------------------------------- registry 参数容错


def test_missing_required_arg_reports_signature(registry: Registry):
    result = registry.invoke("read_file", {})
    assert not result.ok
    assert "path" in result.detail


def test_unknown_arg_dropped(registry: Registry, workspace: Path):
    """模型常自作主张加 explanation / thought 之类的字段。
    为此报错太脆——静默丢弃，让真正的调用继续。"""
    (workspace / "a.py").write_text("pass\n", encoding="utf-8")
    result = registry.invoke(
        "read_file", {"path": "a.py", "explanation": "我要看看这个文件"}
    )
    assert result.ok


def test_stringified_int_coerced(registry: Registry, workspace: Path):
    """模型会把整数写成字符串。为此让一整轮白费不值得。"""
    (workspace / "n.txt").write_text("\n".join("abcdef"), encoding="utf-8")
    result = registry.invoke("read_file", {"path": "n.txt", "limit": "2"})
    assert result.ok
    assert "3\tc" not in result.detail


def test_unknown_tool_name(registry: Registry):
    result = registry.invoke("no_such_tool", {})
    assert not result.ok


def test_schemas_shape(registry: Registry):
    for schema in registry.schemas():
        assert schema["type"] == "function"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"
