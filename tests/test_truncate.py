"""截断。

要验的不是"字数没超"，而是**省略处留下了可恢复的指针**——
这是"丢信息"和"延迟加载"的区别。
"""

from __future__ import annotations

from hako.truncate import clip_lines, clip_text


def test_short_text_untouched():
    assert clip_text("hello", 100) == "hello"


def test_exact_budget_untouched():
    assert clip_text("x" * 100, 100) == "x" * 100


def test_keeps_both_head_and_tail():
    """只留头会丢掉 traceback 和 exit 状态，那恰恰是最该看的部分。"""
    text = "HEAD_MARKER" + "m" * 5000 + "TAIL_MARKER"
    out = clip_text(text, 400)
    assert "HEAD_MARKER" in out
    assert "TAIL_MARKER" in out
    assert "m" * 5000 not in out


def test_elision_marker_states_how_much_was_lost():
    out = clip_text("a" * 3000, 200)
    assert "已省略" in out
    assert "3000" in out          # 原文总量，模型据此判断要不要换更精确的命令


def test_hint_is_carried_into_marker():
    out = clip_text("a" * 3000, 200, hint="用 grep 缩小范围")
    assert "用 grep 缩小范围" in out


def test_tail_ratio_respected():
    out = clip_text("H" * 1000 + "T" * 1000, 400, tail_ratio=0.5)
    assert out.count("T") >= 190


# ------------------------------------------------------------------ clip_lines


def test_line_numbers_start_at_one():
    body, has_more = clip_lines(
        ["a", "b"], 10, start_line=1, total_lines=2, path="f.py"
    )
    assert "1\ta" in body
    assert "2\tb" in body
    assert has_more is False


def test_offset_window_numbers_are_absolute():
    """读第 101 行开始时行号得是 101，不能从 1 重新数——
    模型要拿这个行号和人对照。"""
    body, _ = clip_lines(
        ["x"], 10, start_line=101, total_lines=200, path="f.py"
    )
    assert "101\tx" in body


def test_has_more_true_when_window_ends_early():
    body, has_more = clip_lines(
        ["a"] * 10, 10, start_line=1, total_lines=50, path="f.py"
    )
    assert has_more is True
    assert "还有 40 行" in body
    # 指针必须是"下一次从哪读"，而不是含糊的"还有更多"
    assert "offset=10" in body


def test_has_more_false_on_last_page():
    body, has_more = clip_lines(
        ["a"] * 10, 10, start_line=41, total_lines=50, path="f.py"
    )
    assert has_more is False
    assert "需要继续读" not in body


def test_pointer_offset_is_resumable():
    """指针给的 offset 必须刚好接上：第一页读 0..9，下一页从 10 起。"""
    total = 30
    body, _ = clip_lines(
        ["l"] * 10, 10, start_line=1, total_lines=total, path="f.py"
    )
    assert "offset=10" in body

    body2, has_more2 = clip_lines(
        ["l"] * 10, 10, start_line=11, total_lines=total, path="f.py"
    )
    assert "11\tl" in body2
    assert "offset=20" in body2
    assert has_more2 is True


def test_empty_file():
    body, has_more = clip_lines([], 10, start_line=1, total_lines=0, path="f.py")
    assert body == ""
    assert has_more is False
