"""跨平台单键读取与选择菜单。

自己写而不引 readchar / questionary（见 DESIGN.md #9）：
核心就是两个平台各十几行，而且这样能精确控制"非 tty 时怎么退化"——
第三方库在管道输入下往往直接抛异常，而评测是重定向 stdin 跑的，
必须能优雅退化成默认值而不是崩掉。
"""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import msvcrt
else:
    try:
        import termios
        import tty
    except ImportError:  # 极少数无 termios 的环境
        termios = tty = None  # type: ignore[assignment]


def stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def read_key() -> str:
    """阻塞读一个按键，返回归一化后的名字。

    返回值：'up' | 'down' | 'enter' | 'esc' | 'ctrl-c' | 单个小写字符
    """
    if not stdin_is_tty():
        return "enter"                      # 非交互环境：一律当作"接受默认"

    return _read_key_windows() if IS_WINDOWS else _read_key_posix()


def _read_key_windows() -> str:
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):              # 功能键前缀，后面跟真正的扫描码
        code = msvcrt.getwch()
        return {"H": "up", "P": "down"}.get(code, "")
    return _normalize(ch)


def _read_key_posix() -> str:
    if termios is None:
        return "enter"
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":                    # ESC：可能是方向键的转义序列
            seq = sys.stdin.read(2)
            if seq == "[A":
                return "up"
            if seq == "[B":
                return "down"
            return "esc"
        return _normalize(ch)
    finally:
        # 必须恢复终端设置，否则异常退出后用户的 shell 会变成不回显的哑终端
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _normalize(ch: str) -> str:
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x03":
        return "ctrl-c"
    if ch == "\x1b":
        return "esc"
    return ch.lower()


def select(console, title: str, options: list[str], default: int = 0) -> int:
    """方向键 + 回车的单选菜单。返回选中项下标。

    非 tty 时直接返回 default，不做任何渲染。
    """
    if not stdin_is_tty():
        return default

    index = default
    console.print(title)
    # 先占位，之后用 ANSI 光标上移原地重绘这几行——
    # 只重绘选项区，上方的 transcript 不受影响（这正是 inline 模式的意义）。
    for _ in options:
        console.print("")

    while True:
        sys.stdout.write(f"\x1b[{len(options)}A")     # 光标上移 N 行
        for i, option in enumerate(options):
            marker = "❯" if i == index else " "
            style = "bold cyan" if i == index else "dim"
            sys.stdout.write("\x1b[2K")               # 清整行
            console.print(f" {marker} {option}", style=style, highlight=False)
        sys.stdout.flush()

        key = read_key()
        if key == "up":
            index = (index - 1) % len(options)
        elif key == "down":
            index = (index + 1) % len(options)
        elif key == "enter":
            return index
        elif key in ("esc", "ctrl-c"):
            return default
        elif key.isdigit() and 1 <= int(key) <= len(options):
            return int(key) - 1
