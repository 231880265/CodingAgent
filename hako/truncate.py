"""工具结果截断：上下文是稀缺资源。

核心设计原则（见 DESIGN.md #4）：
**省略处必须留下可恢复的指针。** 只说"内容被截断了"，模型只知道信息没了、
不知道怎么找回来，于是要么瞎猜要么放弃；给出 offset 续读方式或落盘路径，
截断就从"丢信息"变成了"延迟加载"。

保留头 + 尾而不是只留头：命令输出的关键信息往往在两端——开头是它在做什么，
结尾是 traceback 和 exit 状态。中间的编译进度条没人关心。
"""

from __future__ import annotations


def clip_text(
    text: str,
    budget: int,
    *,
    tail_ratio: float = 0.25,
    hint: str = "",
) -> str:
    """按字符预算裁剪，保留头尾，中间插入带指针的省略标记。"""
    if len(text) <= budget:
        return text

    tail_size = int(budget * tail_ratio)
    head_size = budget - tail_size
    head, tail = text[:head_size], text[-tail_size:]

    elided = len(text) - head_size - tail_size
    marker = f"\n\n… 已省略中间 {elided} 个字符（原文共 {len(text)} 字符）"
    if hint:
        marker += f"；{hint}"
    marker += " …\n\n"

    return head + marker + tail


def clip_lines(
    lines: list[str],
    max_lines: int,
    *,
    start_line: int,
    total_lines: int,
    path: str,
) -> tuple[str, bool]:
    """按行裁剪文件读取结果，带行号。

    返回 (渲染文本, 后面是否还有内容)。行号从 1 开始，方便模型和人对照。

    第二个返回值是 has_more 而不是"本次是否裁剪"：调用方通常已经按窗口切过片，
    真正要告诉模型的是"还剩没读的"——那才决定它下一步要不要续读。
    """
    shown = lines[:max_lines]
    last_line = start_line - 1 + len(shown)
    has_more = last_line < total_lines

    width = len(str(max(last_line, 1)))
    body = "\n".join(
        f"{start_line + i:>{width}}\t{line}" for i, line in enumerate(shown)
    )

    if has_more:
        remaining = total_lines - last_line
        body += (
            f"\n\n… 该文件还有 {remaining} 行未显示（共 {total_lines} 行）。"
            f"需要继续读：read_file(path={path!r}, offset={last_line}) …"
        )
    return body, has_more
