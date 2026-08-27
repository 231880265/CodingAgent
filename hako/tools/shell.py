"""命令执行工具。

设计决策（见 DESIGN.md #6）：走 shell 但不用 `shell=True`。
构造成 argv 数组 `[shell, flag, 整条命令]`——模型的命令字符串作为**单个参数**
交给 shell，避免 Python 和 shell 两层解释叠加带来的转义歧义，同时保留
管道、重定向这些 agent 真正需要的 shell 语义。

而内部自己发起的命令（如 git checkpoint）一律用纯 argv 数组、绝不拼字符串——
那里的参数含模型输出，拼进 shell 字符串就是注入面。区别在于：
run_command 的输入本来就是"一条要执行的命令"，防护靠权限层；
内部命令的输入是"数据"，防护靠不进 shell。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..truncate import clip_text
from .base import Tool, ToolError, ToolResult

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600

# 交互式命令：agent 答不了 y/n 提示，会直接把自己挂死到超时。
# 提前拦下来并告诉模型怎么改，比让它等 60 秒再失败强。
INTERACTIVE_HINTS = {
    "npm run dev": "改用 npm run build，或加 --if-present 跑非阻塞脚本",
    "npm create": "加 --yes",
    "vim": "用 read_file / edit_file 代替编辑器",
    "nano": "用 read_file / edit_file 代替编辑器",
    "git rebase -i": "去掉 -i，交互式 rebase 无法在 agent 中完成",
    "git add -i": "去掉 -i",
    "python -i": "去掉 -i，改用 python -c 或写成脚本文件",
    "ssh ": "agent 环境无法处理交互式认证",
}

# 危险模式：需要用户显式批准（权限层实现，这里只负责识别）
DANGER_PATTERNS = (
    "rm -rf", "rm -fr", "rmdir /s", "remove-item -recurse",
    "git push", "git reset --hard", "git clean -f",
    "curl", "wget", "iwr", "invoke-webrequest",   # 下载 + 管道执行
    "shutdown", "reboot", "mkfs", "dd if=",
    "format ", "del /f", "drop table", "drop database",
)


# PowerShell 自己的报错文本走 .NET Console，不受 PYTHONIOENCODING 管。
# 不改这个，中文 Windows 上一条语法错误回给模型的就是一串 cp936 乱码，
# 模型只能瞎猜。前缀里改掉 Console.OutputEncoding 才能覆盖到 shell 自身的输出。
_PS_UTF8_PRELUDE = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    "$OutputEncoding=[Text.Encoding]::UTF8; "
)


def shell_argv(command: str) -> list[str]:
    """把命令包装成 argv 数组。Windows 走 PowerShell，POSIX 走 sh。"""
    if sys.platform == "win32":
        # -NoProfile：不加载用户 profile，保证行为可复现（否则用户的 alias 会干扰）
        return [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _PS_UTF8_PRELUDE + command,
        ]
    return ["/bin/sh", "-c", command]


def looks_interactive(command: str) -> str | None:
    """返回替代建议，或 None。"""
    low = command.lower()
    for pattern, hint in INTERACTIVE_HINTS.items():
        if pattern in low:
            return hint
    return None


def is_dangerous(command: str) -> str | None:
    """返回命中的危险模式，或 None。"""
    low = command.lower()
    for pattern in DANGER_PATTERNS:
        if pattern in low:
            return pattern
    return None


def make_run_command(workspace: Path, budget: int = 6000) -> Tool:
    def handler(command: str, timeout: int = DEFAULT_TIMEOUT) -> ToolResult:
        command = (command or "").strip()
        if not command:
            raise ToolError("command 不能为空")

        if hint := looks_interactive(command):
            raise ToolError(
                f"该命令需要交互输入，会导致 agent 卡死到超时：{command}\n建议：{hint}"
            )

        timeout = max(1, min(int(timeout), MAX_TIMEOUT))
        # 强制 UTF-8，否则 Windows 上 cp936 会把子进程输出解码成乱码
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

        try:
            proc = subprocess.run(
                shell_argv(command),
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
                # stdin 接空设备：命令若仍试图读输入会立刻 EOF 失败，
                # 而不是静默等到超时。快速失败优于慢速失败。
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(
                f"命令超时（{timeout}s）已被终止：{command}\n"
                "若确实需要更久，重试时显式传更大的 timeout；"
                "若这是个常驻进程（dev server 等），换成一次性命令。"
            ) from None
        except FileNotFoundError:
            raise ToolError(f"找不到 shell 或命令不存在：{command}") from None

        parts = []
        if proc.stdout.strip():
            parts.append(proc.stdout.rstrip())
        if proc.stderr.strip():
            parts.append(f"--- stderr ---\n{proc.stderr.rstrip()}")
        output = "\n".join(parts) or "(无输出)"

        detail = clip_text(
            output, budget, hint="完整输出未保留，需要时请用更精确的命令重跑（如加 grep / --tb=short）"
        )
        ok = proc.returncode == 0
        detail = f"exit={proc.returncode}\n{detail}"

        return ToolResult(
            ok=ok,
            detail=detail,
            summary=f"{command[:60]}  exit={proc.returncode}",
        )

    return Tool(
        name="run_command",
        description=(
            "在工作目录下执行 shell 命令并返回 stdout/stderr 与退出码"
            f"（Windows 为 PowerShell，其余为 sh）。默认超时 {DEFAULT_TIMEOUT} 秒。"
            "不要执行需要交互输入或常驻不退出的命令（如 dev server）。"
            "验证改动请优先跑项目自带的测试命令。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout": {
                    "type": "integer",
                    "description": f"超时秒数，默认 {DEFAULT_TIMEOUT}，上限 {MAX_TIMEOUT}",
                },
            },
            "required": ["command"],
        },
        handler=handler,
        needs_approval=True,
    )
