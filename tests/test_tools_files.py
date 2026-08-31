"""文件工具与 registry 的参数容错。"""

from __future__ import annotations

from pathlib import Path

from hako.tools import Registry


def read(registry: Registry, **args):
    return registry.invoke("read_file", args)


def edit(registry: Registry, **args):
    return registry.invoke("edit_file", args)


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


# ------------------------------------------------------- edit_file


def test_edit_replaces_one_unique_match(registry: Registry, workspace: Path):
    target = workspace / "calculator.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    result = edit(
        registry,
        path="./calculator.py",
        old_text="    return a - b",
        new_text="    return a + b",
    )

    assert result.ok
    assert result.touched_paths == ("calculator.py",)
    assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_edit_zero_match_returns_candidate_without_writing(
    registry: Registry, workspace: Path
):
    target = workspace / "a.py"
    original = "def total():\n    return left + right\n"
    target.write_text(original, encoding="utf-8")

    result = edit(
        registry,
        path="a.py",
        old_text="    return left - right",
        new_text="    return left * right",
    )

    assert not result.ok
    assert "匹配 0 处" in result.detail
    assert "最接近候选" in result.detail
    assert "left + right" in result.detail
    assert target.read_text(encoding="utf-8") == original


def test_edit_multiple_matches_refuses_to_guess(registry: Registry, workspace: Path):
    target = workspace / "a.py"
    original = "value = 1\nkeep = 0\nvalue = 1\n"
    target.write_text(original, encoding="utf-8")

    result = edit(
        registry, path="a.py", old_text="value = 1", new_text="value = 2"
    )

    assert not result.ok
    assert "匹配 2 处" in result.detail
    assert "1, 3" in result.detail
    assert target.read_text(encoding="utf-8") == original


def test_edit_detects_overlapping_matches(registry: Registry, workspace: Path):
    target = workspace / "a.txt"
    target.write_text("aaa", encoding="utf-8")
    result = edit(registry, path="a.txt", old_text="aa", new_text="b")
    assert not result.ok
    assert "匹配 2 处" in result.detail
    assert target.read_text(encoding="utf-8") == "aaa"


def test_edit_preserves_crlf_when_model_sends_lf(registry: Registry, workspace: Path):
    target = workspace / "windows.py"
    target.write_bytes(b"start\r\nold\r\nend\r\n")

    result = edit(
        registry,
        path="windows.py",
        old_text="old\n",
        new_text="new\n",
    )

    assert result.ok
    assert target.read_bytes() == b"start\r\nnew\r\nend\r\n"


def test_edit_preserves_existing_gbk_encoding(registry: Registry, workspace: Path):
    target = workspace / "legacy.py"
    target.write_bytes("# 中文\nvalue = 1\n".encode("gbk"))
    result = edit(
        registry, path="legacy.py", old_text="value = 1", new_text="value = 2"
    )
    assert result.ok
    assert target.read_bytes().decode("gbk") == "# 中文\nvalue = 2\n"


def test_edit_noop_does_not_claim_a_touch(registry: Registry, workspace: Path):
    (workspace / "a.py").write_text("pass\n", encoding="utf-8")
    result = edit(registry, path="a.py", old_text="pass", new_text="pass")
    assert result.ok
    assert result.touched_paths == ()
    assert "无变化" in result.summary


def test_edit_escape_and_empty_locator_rejected(registry: Registry, workspace: Path):
    (workspace / "a.py").write_text("pass\n", encoding="utf-8")
    escaped = edit(
        registry, path="../a.py", old_text="pass", new_text="raise SystemExit"
    )
    empty = edit(registry, path="a.py", old_text="", new_text="x")
    assert not escaped.ok
    assert not empty.ok
    assert "old_text 不能为空" in empty.detail


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


def test_claude_read_file_alias_is_normalized(registry: Registry, workspace: Path):
    """Claude Code 的 Read 使用 file_path；不能因此陷入重复失败。"""
    (workspace / "claude.py").write_text("value = 1\n", encoding="utf-8")

    result = registry.invoke("read_file", {"file_path": "claude.py"})

    assert result.ok
    assert result.subject_path == "claude.py"
    assert "value = 1" in result.detail


def test_claude_write_file_alias_is_normalized(registry: Registry, workspace: Path):
    result = registry.invoke(
        "write_file",
        {"file_path": "created.py", "content": "created = True\n"},
    )

    assert result.ok
    assert result.touched_paths == ("created.py",)
    assert (workspace / "created.py").read_text(encoding="utf-8") == "created = True\n"


def test_claude_edit_aliases_are_normalized(registry: Registry, workspace: Path):
    target = workspace / "service.py"
    target.write_text("published = False\n", encoding="utf-8")

    result = registry.invoke(
        "edit_file",
        {
            "file_path": "service.py",
            "old_string": "published = False",
            "new_string": "published = True",
        },
    )

    assert result.ok
    assert result.touched_paths == ("service.py",)
    assert target.read_text(encoding="utf-8") == "published = True\n"


def test_claude_edit_replace_all_false_is_accepted(
    registry: Registry, workspace: Path
):
    target = workspace / "safe.py"
    target.write_text("enabled = False\n", encoding="utf-8")

    result = registry.invoke(
        "edit_file",
        {
            "file_path": "safe.py",
            "old_string": "enabled = False",
            "new_string": "enabled = True",
            "replace_all": False,
        },
    )

    assert result.ok
    assert target.read_text(encoding="utf-8") == "enabled = True\n"


def test_claude_edit_replace_all_true_preserves_unique_edit_safety(
    registry: Registry, workspace: Path
):
    target = workspace / "unsafe.py"
    target.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    result = registry.invoke(
        "edit_file",
        {
            "file_path": "unsafe.py",
            "old_string": "value = 1",
            "new_string": "value = 2",
            "replace_all": True,
        },
    )

    assert not result.ok
    assert "replace_all=true" in result.detail
    assert target.read_text(encoding="utf-8") == "value = 1\nvalue = 1\n"


def test_conflicting_canonical_and_alias_args_are_rejected(
    registry: Registry, workspace: Path
):
    (workspace / "a.py").write_text("a = 1\n", encoding="utf-8")
    (workspace / "b.py").write_text("b = 1\n", encoding="utf-8")

    result = registry.invoke(
        "read_file",
        {"path": "a.py", "file_path": "b.py"},
    )

    assert not result.ok
    assert "值不一致" in result.detail


def test_model_aliases_are_not_published_in_tool_schema(registry: Registry):
    schemas = {
        schema["function"]["name"]: schema["function"]["parameters"]
        for schema in registry.schemas()
    }

    assert "file_path" not in schemas["read_file"]["properties"]
    assert "old_string" not in schemas["edit_file"]["properties"]
    assert "new_string" not in schemas["edit_file"]["properties"]


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
