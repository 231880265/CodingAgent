"""控制台编码。

Windows 上这不是"优化"，是能不能用的问题：控制台默认 cp936，
渲染器用的 ◇ ▓ ✓ 一个都编码不了，rich 会抛 UnicodeEncodeError。
而事件总线刻意吞掉订阅者异常（渲染失败不该炸掉任务），两者一叠加，
症状就是**整个 transcript 静默消失**——最难查的那种 bug。

所以两手都要做：
1. 能重配置成 UTF-8 就重配置（setup_console）；
2. 重配置不了就换成 ASCII 字形（supports_unicode 决定），而不是硬撞上去。

第 2 条不是多余的。重定向到文件、CI 日志、以及被别的程序用管道接走时，
编码由对方决定，我们改不了。
"""

from __future__ import annotations

import sys
from typing import IO, Any

# 探测用字符集：渲染器实际会输出的那几类——几何图形、块元素、勾叉、中文。
_PROBE = "◇▸◆▓░✓✗⚠⊘│└中"


def setup_console() -> None:
    """把 stdout/stderr 切成 UTF-8。必须在任何输出之前调用。"""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # 被替换成 StringIO（pytest capture）或已关闭时会走到这里，忽略即可
            pass


def supports_unicode(stream: IO[Any] | None = None) -> bool:
    """该流能否编码渲染器要用的字符。"""
    stream = stream if stream is not None else sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        _PROBE.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True
