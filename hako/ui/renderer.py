"""rich 渲染器：事件总线的一个订阅者。

用 inline 模式而不是 textual 全屏（见 DESIGN.md #9）：
全屏会切 alternate screen buffer，退出后 scrollback 全丢，
而"翻回去看 agent 三十步前读了什么"恰恰是排查问题时最需要的操作。
所以这里只做两件事：向下追加 transcript，以及原地刷新"当前正在做什么"。
"""

from __future__ import annotations

from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from .. import events as ev
from ..tools import Tool
from .console import supports_unicode
from .keys import select, stdin_is_tty

# 工具名 → 图标。写操作用不同符号，让"这一步动了磁盘"在 transcript 里一眼可见。
ICONS = {
    "read_file": "◇",
    "list_dir": "◇",
    "run_command": "▸",
    "write_file": "◆",
    "edit_file": "◆",
}
# 编码不支持时的退路。字形退化好过整块输出消失（见 ui/console.py）。
ASCII_ICONS = {
    "read_file": "-",
    "list_dir": "-",
    "run_command": ">",
    "write_file": "*",
    "edit_file": "*",
}
GLYPHS = {
    "fail": "✗", "ok": "✓", "warn": "⚠", "denied": "⊘",
    "bar_on": "▓", "bar_off": "░", "pipe": "│", "corner": "└",
    "default_tool": "▸",
}
ASCII_GLYPHS = {
    "fail": "x", "ok": "v", "warn": "!", "denied": "o",
    "bar_on": "#", "bar_off": ".", "pipe": "|", "corner": "+",
    "default_tool": ">",
}
DETAIL_LINES = 6          # 每个工具结果最多展开几行，其余折叠


class Renderer:
    def __init__(self, console: Console | None = None, verbose: bool = False) -> None:
        self.console = console or Console()
        self.verbose = verbose
        self._status = None
        self._step = 0
        # 只探测一次：编码在进程存活期内不会变
        self._unicode = supports_unicode(getattr(self.console, "file", None))
        self._icons = ICONS if self._unicode else ASCII_ICONS
        self._g = GLYPHS if self._unicode else ASCII_GLYPHS

    # ------------------------------------------------------------ 事件入口

    def handle(self, event: ev.Event) -> None:
        handler = getattr(self, f"_on_{event.kind}", None)
        if handler is not None:
            handler(event)

    # ------------------------------------------------------------ 各事件

    def _on_run_started(self, e: ev.RunStarted) -> None:
        self.console.print()
        self.console.print(Text("  hako", style="bold cyan"), end="")
        self.console.print(Text(f"  {e.model}  ·  {e.cwd}", style="dim"))
        self.console.print()
        self.console.print(Text(f"  {e.task}", style="bold"))
        self.console.print()

    def _on_turn_started(self, e: ev.TurnStarted) -> None:
        self._step = e.step
        self._start_thinking()

    def _on_assistant_text(self, e: ev.AssistantText) -> None:
        self._stop_thinking()
        for line in e.text.splitlines():
            self.console.print(f"  {line}" if line.strip() else "")
        self.console.print()

    def _on_tool_call_started(self, e: ev.ToolCallStarted) -> None:
        self._stop_thinking()
        icon = self._icons.get(e.name, self._g["default_tool"])
        self.console.print(
            Text(f"  {icon} ", style="cyan")
            + Text(e.name, style="bold cyan")
            + Text(f"  {_format_args(e.name, e.args)}", style="dim"),
            highlight=False,
        )

    def _on_tool_call_finished(self, e: ev.ToolCallFinished) -> None:
        if e.ok:
            self.console.print(Text(f"    {e.summary}", style="dim"))
            if self.verbose:
                self._print_detail(e.detail)
        else:
            # 失败要显眼但不刺眼：这是循环的正常组成部分，不是崩溃
            self.console.print(
                Text(f"    {self._g['fail']} ", style="yellow")
                + Text(e.summary, style="yellow")
            )
            self._print_detail(e.detail, style="yellow dim")

    def _on_context_stats(self, e: ev.ContextStats) -> None:
        if not self.verbose:
            return
        bar = _context_bar(
            e.used_tokens, e.limit, on=self._g["bar_on"], off=self._g["bar_off"]
        )
        self.console.print(Text(f"    {bar}", style="dim"))

    def _on_agent_error(self, e: ev.AgentError) -> None:
        self._stop_thinking()
        self.console.print()
        self.console.print(Text(f"  {self._g['fail']} {e.message}", style="bold red"))

    def _on_run_finished(self, e: ev.RunFinished) -> None:
        self._stop_thinking()
        g = self._g
        label = {
            "done": (f"{g['ok']} 完成", "green"),
            "max_steps": (f"{g['warn']} 达到步数上限", "yellow"),
            "stuck": (f"{g['warn']} 检测到无进展，已终止", "yellow"),
            "denied": (f"{g['denied']} 用户已拒绝", "yellow"),
            "error": (f"{g['fail']} 出错终止", "red"),
        }.get(e.reason, (e.reason, "dim"))

        self.console.print()
        self.console.print(
            Text(f"  {label[0]}", style=f"bold {label[1]}")
            + Text(f"   {e.steps} 步 · {e.total_tokens:,} tokens", style="dim")
        )
        self.console.print()

    # ------------------------------------------------------------ 辅助

    def _start_thinking(self) -> None:
        if self._status is None and stdin_is_tty():
            self._status = self.console.status(
                Text(f"思考中… (第 {self._step} 步)", style="dim"), spinner="dots"
            )
            self._status.start()

    def _stop_thinking(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _print_detail(self, detail: str, style: str = "dim") -> None:
        lines = detail.splitlines()
        for line in lines[:DETAIL_LINES]:
            self.console.print(
                Text(f"    {self._g['pipe']} {line[:160]}", style=style), highlight=False
            )
        if len(lines) > DETAIL_LINES:
            self.console.print(
                Text(
                    f"    {self._g['corner']} 另有 {len(lines) - DETAIL_LINES} 行（--verbose 查看）",
                    style="dim",
                )
            )


def _format_args(name: str, args: dict) -> str:
    """把参数压成一行。命令和路径是人最关心的，其余省略。"""
    if name == "run_command":
        return str(args.get("command", ""))[:80]
    path = args.get("path", "")
    extra = ""
    if name == "read_file" and args.get("offset"):
        extra = f"  offset={args['offset']}"
    return f"{path}{extra}"


def _context_bar(
    used: int, limit: int, width: int = 12, on: str = "▓", off: str = "░"
) -> str:
    ratio = min(1.0, used / limit) if limit else 0.0
    filled = int(ratio * width)
    return f"ctx {used / 1000:.1f}k/{limit // 1000}k  {on * filled}{off * (width - filled)}"


# ---------------------------------------------------------------- 批准


def make_approval_fn(console: Console, auto_approve: bool = False):
    """生成批准回调。

    默认询问而非默认放行（见 DESIGN.md #6）：写文件和执行命令是不可逆的，
    "先问一句"的成本远低于"改错了再回滚"。
    """
    remembered: set[str] = set()

    def approve(tool: Tool, args: dict) -> bool:
        if auto_approve or tool.name in remembered:
            return True
        if not stdin_is_tty():
            return True                    # 评测/管道模式：无人可问，放行

        console.print()
        console.print(Text(f"  {tool.name} 请求执行：", style="bold yellow"))
        preview = _format_args(tool.name, args)
        if tool.name == "write_file":
            console.print(
                Syntax(
                    str(args.get("content", ""))[:800],
                    _guess_lexer(str(args.get("path", ""))),
                    theme="ansi_dark",
                    line_numbers=False,
                    word_wrap=True,
                )
            )
        else:
            console.print(Text(f"    {preview}", style="yellow"))

        choice = select(
            console,
            Text("  是否执行？", style="dim"),
            ["允许这一次", f"允许本次会话内所有 {tool.name}", "拒绝"],
        )
        console.print()
        if choice == 1:
            remembered.add(tool.name)
        return choice in (0, 1)

    return approve


def _guess_lexer(path: str) -> str:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python", "js": "javascript", "ts": "typescript",
        "json": "json", "md": "markdown", "html": "html",
        "css": "css", "sh": "bash", "yml": "yaml", "yaml": "yaml",
    }.get(suffix, "text")
