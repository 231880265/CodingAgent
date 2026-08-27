"""arguments 解析。

每个 case 都是实测中真实见过的畸形输出。原生 tool calling 不保证
arguments 是合法 JSON —— 能修就修，修不了把错误回传，不让一整轮白费。
"""

from __future__ import annotations

from hako.llm import estimate_tokens, parse_arguments


def ok(raw: str) -> dict:
    args, error = parse_arguments(raw)
    assert not error, f"本该能解析：{raw!r} → {error}"
    return args


def test_plain_object():
    assert ok('{"path": "a.py"}') == {"path": "a.py"}


def test_empty_arguments_is_legal():
    """无参工具（list_dir 不带参数）会回空串，这不是错误。"""
    for raw in ("", "   ", "{}"):
        args, error = parse_arguments(raw)
        assert args == {} and not error


def test_none_arguments():
    args, error = parse_arguments(None)  # type: ignore[arg-type]
    assert args == {} and not error


def test_markdown_fence_stripped():
    assert ok('```json\n{"path": "a.py"}\n```') == {"path": "a.py"}


def test_bare_fence_stripped():
    assert ok('```\n{"path": "a.py"}\n```') == {"path": "a.py"}


def test_trailing_comma_repaired():
    assert ok('{"path": "a.py", "limit": 10,}') == {"path": "a.py", "limit": 10}


def test_trailing_comma_in_array():
    assert ok('{"paths": ["a", "b",]}') == {"paths": ["a", "b"]}


def test_unclosed_brace_repaired():
    """流式截断的典型形态。"""
    assert ok('{"path": "a.py"') == {"path": "a.py"}


def test_unclosed_string_and_brace_repaired():
    args = ok('{"path": "a.py')
    assert args["path"].startswith("a.py")


def test_double_encoded_json_string():
    """有的兼容端点把整个对象再序列化一次塞进 arguments。"""
    assert ok('"{\\"path\\": \\"a.py\\"}"') == {"path": "a.py"}


def test_chinese_content_preserved():
    assert ok('{"content": "中文内容"}') == {"content": "中文内容"}


def test_nested_object_preserved():
    assert ok('{"edits": [{"old": "a", "new": "b"}]}')["edits"][0]["old"] == "a"


# ------------------------------------------------------------------ 放弃的情形


def test_json_array_rejected_with_reason():
    """能解析但不是对象 —— 报错要说清实际是什么类型，模型才知道怎么改。"""
    args, error = parse_arguments('["a.py"]')
    assert args == {} and "对象" in error and "list" in error


def test_number_rejected():
    args, error = parse_arguments("42")
    assert args == {} and error


def test_plain_prose_rejected():
    """模型有时干脆用自然语言回答。修不了就回传错误让它重发。"""
    args, error = parse_arguments("我需要先读一下这个文件")
    assert args == {} and "不是合法 JSON" in error


def test_error_message_is_length_capped():
    """错误信息本身也会进上下文，不能把 50KB 垃圾整段贴回去。"""
    args, error = parse_arguments("!" * 50000)
    assert args == {} and len(error) < 400


# ------------------------------------------------------------------ token 估算


def test_estimate_counts_cjk_heavier_than_ascii():
    assert estimate_tokens("中" * 100) > estimate_tokens("a" * 100)


def test_estimate_never_zero():
    assert estimate_tokens("") >= 1


def test_estimate_scales_with_length():
    assert estimate_tokens("a" * 4000) > estimate_tokens("a" * 40)
