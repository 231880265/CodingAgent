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
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from ..fs_audit import diff_snapshots, snapshot_workspace
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

# 危险模式是保守的词法门禁，不假装自己是完整 shell AST。误报只多一次确认，
# 漏报却可能造成不可恢复操作，因此覆盖参数顺序变化并保留明确的命中原因。
DANGER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "递归/强制删除",
        re.compile(
            r"(?i)(?:\brm(?:\.exe)?\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)[a-z]*\b|"
            r"\brmdir(?:\.exe)?\s+/s\b|"
            r"\bremove-item\b[^\r\n;&|]*(?:^|\s)-recurse\b|\bdel(?:\.exe)?\s+/f\b)"
        ),
    ),
    (
        "会改写远端或丢弃版本库状态的 Git 命令",
        re.compile(
            r"(?i)\bgit(?:\.exe)?\s+(?:push\b|reset\s+--hard\b|clean\s+-[a-z]*f[a-z]*\b)"
        ),
    ),
    (
        "网络下载命令",
        re.compile(r"(?i)(?:^|[\s;&|])(?:curl|wget|iwr|invoke-webrequest)(?:\.exe)?\b"),
    ),
    (
        "系统或磁盘破坏命令",
        re.compile(
            r"(?i)(?:\bshutdown\b|\breboot\b|\bmkfs(?:\.\w+)?\b|"
            r"\bdd\s+[^\r\n;&|]*\bif=|\bformat(?:\.com)?\s+[a-z]:)"
        ),
    ),
    (
        "数据库破坏语句",
        re.compile(r"(?i)\bdrop\s+(?:table|database)\b"),
    ),
)

# Verified Finish 只接受“单一、可审计”的验证命令。含管道、命令拼接或显式
# 吞错的命令即使最终 exit=0 也不计入，避免 `pytest || true` 伪造通过。
_COMPOUND_COMMAND = re.compile(r"[;&|\r\n]")
_NON_EXECUTING_CHECK = re.compile(
    r"(?i)(?:--collect-only|(?:^|\s)--co(?:\s|$)|(?:^|\s)--help(?:\s|$)|"
    r"(?:^|\s)-h(?:\s|$)|(?:^|\s)--version(?:\s|$)|--list-tests?|"
    r"(?:^|\s)-N(?:\s|$))"
)
_VERIFICATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "test",
        re.compile(
            r"(?i)^\s*(?:(?:\S*python(?:\.exe)?|py(?:\.exe)?)\s+(?:-B\s+)?-m\s+"
            r"(?:pytest|unittest)|\S*pytest(?:\.exe)?|npm(?:\.cmd)?\s+"
            r"(?:run\s+)?test|pnpm\s+(?:run\s+)?test|yarn\s+(?:run\s+)?test|"
            r"cargo\s+test|go\s+test|dotnet\s+test|mvnw?(?:\.cmd)?\s+.*\btest|"
            r"gradlew?(?:\.bat)?\s+.*\btest|ctest(?:\.exe)?(?:\s|$)|"
            r"meson\s+test\b|java(?:\.exe)?\s+(?:-jar\s+\S*junit-platform-console\S*|"
            r"org\.junit\.platform\.console\.ConsoleLauncher)\b)"
        ),
    ),
    (
        "build",
        re.compile(
            r"(?i)^\s*(?:npm(?:\.cmd)?\s+run\s+build|pnpm\s+(?:run\s+)?build|"
            r"yarn\s+(?:run\s+)?build|cargo\s+build|go\s+build|dotnet\s+build|"
            r"mvnw?(?:\.cmd)?\s+.*\bpackage|gradlew?(?:\.bat)?\s+.*\bbuild|"
            r"(?:\S*python(?:\.exe)?|py(?:\.exe)?)\s+(?:-B\s+)?-m\s+compileall|"
            r"(?:\S*[\\/])?(?:g\+\+|clang\+\+|c\+\+)(?:\.exe)?\s+.*\.(?:c|cc|cpp|cxx)\b|"
            r"(?:\S*[\\/])?cl(?:\.exe)?\s+.*\.(?:c|cc|cpp|cxx)\b|"
            r"(?:\S*[\\/])?javac(?:\.exe)?\s+.*(?:\.java\b|@\S+)|"
            r"cmake(?:\.exe)?\s+--build\b|(?:mingw32-)?make(?:\.exe)?(?:\s|$)|"
            r"ninja(?:\.exe)?(?:\s|$)|msbuild(?:\.exe)?\s+\S+)"
        ),
    ),
    (
        "check",
        re.compile(
            r"(?i)^\s*(?:\S*ruff(?:\.exe)?\s+check|\S*mypy(?:\.exe)?|"
            r"\S*eslint(?:\.cmd)?|\S*tsc(?:\.cmd)?\s+--noemit|cargo\s+check|go\s+vet)\b"
        ),
    ),
)

_BARE_PYTEST = re.compile(r"(?i)^pytest(?:\.exe)?(?P<args>(?:\s+.*)?)$")


class _CommandCancelled(RuntimeError):
    """协作式取消命令；不是工具故障，也不关闭承载 Agent 的 Worker。"""


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    """只终止本次 shell 命令树，保留上层 Python Worker。"""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP 让 PowerShell 及其编译器/测试子进程共享独立组。
        # 先向整组发送 CTRL_BREAK；这比只 kill PowerShell 父进程更可靠，后者会
        # 让 python/java/npm 子进程继续持有 stdout 管道，communicate() 仍会挂住。
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            proc.wait(timeout=1)
            return
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            killed = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if killed.returncode != 0 and proc.poll() is None:
                proc.kill()
        except (OSError, subprocess.SubprocessError):
            proc.kill()
        return

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()


def _run_cancellable(
    argv: list[str],
    *,
    workspace: Path,
    env: dict[str, str],
    timeout: int,
    cancelled: Callable[[], bool],
) -> subprocess.CompletedProcess[str]:
    options: dict[str, object] = {
        "cwd": workspace,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True

    proc = subprocess.Popen(argv, **options)  # type: ignore[arg-type]
    started = time.monotonic()
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=0.1)
            return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if cancelled():
                _terminate_process_tree(proc)
                proc.communicate()
                raise _CommandCancelled from None
            if time.monotonic() - started >= timeout:
                _terminate_process_tree(proc)
                proc.communicate()
                raise subprocess.TimeoutExpired(argv, timeout) from None


def classify_verification(command: str) -> str | None:
    """把可信的单一验证命令分类为 test/build/check，其他返回 None。"""
    candidate = (command or "").strip()
    if not candidate or _COMPOUND_COMMAND.search(candidate):
        return None
    if _NON_EXECUTING_CHECK.search(candidate):
        return None
    for kind, pattern in _VERIFICATION_PATTERNS:
        if pattern.search(candidate):
            return kind
    return None


def normalize_windows_pytest(
    command: str,
    *,
    platform: str | None = None,
    executable: str | None = None,
) -> str:
    """Windows 上把 bare pytest 绑定到启动 hako 的 Python。

    PATH 里的 pytest.exe 可能来自系统 Python，而 hako 运行在 .venv；前者会让
    同一仓库出现不同 sys.path，甚至诱使 Agent 写 conftest.py 修一个不存在的
    项目问题。只改写明确的 bare `pytest[.exe]`，显式解释器命令保持原样。
    """
    candidate = (command or "").strip()
    if (platform or sys.platform) != "win32":
        return candidate
    match = _BARE_PYTEST.fullmatch(candidate)
    if match is None:
        return candidate
    python = executable or sys.executable
    quoted = "'" + python.replace("'", "''") + "'"
    return f"& {quoted} -m pytest{match.group('args')}"


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
    """返回命中的危险类别，或 None。"""
    for reason, pattern in DANGER_PATTERNS:
        if pattern.search(command or ""):
            return reason
    return None


def make_run_command(
    workspace: Path,
    budget: int = 6000,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> Tool:
    is_cancelled = cancelled or (lambda: False)

    def handler(command: str, timeout: int = DEFAULT_TIMEOUT) -> ToolResult:
        requested_command = (command or "").strip()
        if not requested_command:
            raise ToolError("command 不能为空")

        if hint := looks_interactive(requested_command):
            raise ToolError(
                f"该命令需要交互输入，会导致 agent 卡死到超时：{requested_command}\n建议：{hint}"
            )

        executed_command = normalize_windows_pytest(requested_command)
        normalized = executed_command != requested_command

        timeout = max(1, min(int(timeout), MAX_TIMEOUT))
        # 强制 UTF-8，否则 Windows 上 cp936 会把子进程输出解码成乱码
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        before = snapshot_workspace(workspace)

        try:
            proc = _run_cancellable(
                shell_argv(executed_command),
                workspace=workspace,
                timeout=timeout,
                env=env,
                cancelled=is_cancelled,
            )
        except _CommandCancelled:
            effects = diff_snapshots(before, snapshot_workspace(workspace))
            detail = (
                f"命令已由用户取消：{requested_command}\n"
                "只终止了本次命令进程树；Worker 与会话上下文继续保留。"
            )
            if audit_detail := effects.detail():
                detail = f"{detail}\n{audit_detail}"
            verification_kind = classify_verification(requested_command)
            return ToolResult(
                ok=False,
                detail=detail,
                summary=f"{requested_command[:70]}  cancelled · {effects.summary or 'files unchanged'}",
                touched_paths=effects.touched_paths,
                created_paths=effects.created,
                modified_paths=effects.modified,
                deleted_paths=effects.deleted,
                derived_paths=effects.derived_paths,
                verification_kind=verification_kind or "",
                verification_command=requested_command if verification_kind else "",
            )
        except subprocess.TimeoutExpired:
            effects = diff_snapshots(before, snapshot_workspace(workspace))
            detail = (
                f"命令超时（{timeout}s）已被终止：{requested_command}\n"
                "若确实需要更久，重试时显式传更大的 timeout；"
                "若这是个常驻进程（dev server 等），换成一次性命令。"
            )
            if audit_detail := effects.detail():
                detail = f"{detail}\n{audit_detail}"
            verification_kind = classify_verification(requested_command)
            return ToolResult(
                ok=False,
                detail=detail,
                summary=f"{requested_command[:70]}  timeout · {effects.summary or 'files unchanged'}",
                touched_paths=effects.touched_paths,
                created_paths=effects.created,
                modified_paths=effects.modified,
                deleted_paths=effects.deleted,
                derived_paths=effects.derived_paths,
                verification_kind=verification_kind or "",
                verification_command=(
                    executed_command if verification_kind and normalized
                    else requested_command if verification_kind
                    else ""
                ),
            )
        except FileNotFoundError:
            raise ToolError(f"找不到 shell 或命令不存在：{requested_command}") from None

        effects = diff_snapshots(before, snapshot_workspace(workspace))
        parts = []
        if proc.stdout.strip():
            parts.append(proc.stdout.rstrip())
        if proc.stderr.strip():
            parts.append(f"--- stderr ---\n{proc.stderr.rstrip()}")
        output = "\n".join(parts) or "(无输出)"
        if normalized:
            output = (
                "[hako] Windows bare pytest 已使用当前 Python 执行："
                f"{executed_command}\n{output}"
            )
        if audit_detail := effects.detail():
            output = f"{audit_detail}\n{output}"

        detail = clip_text(
            output, budget, hint="完整输出未保留，需要时请用更精确的命令重跑（如加 grep / --tb=short）"
        )
        ok = proc.returncode == 0
        detail = f"exit={proc.returncode}\n{detail}"

        verification_kind = classify_verification(requested_command)
        summary_command = (
            f"{requested_command} [current Python]"
            if normalized
            else requested_command
        )
        effect_summary = f" · {effects.summary}" if effects.summary else ""
        return ToolResult(
            ok=ok,
            detail=detail,
            summary=f"{summary_command[:80]}  exit={proc.returncode}{effect_summary}",
            touched_paths=effects.touched_paths,
            created_paths=effects.created,
            modified_paths=effects.modified,
            deleted_paths=effects.deleted,
            derived_paths=effects.derived_paths,
            verification_kind=verification_kind or "",
            verification_command=(
                executed_command if verification_kind and normalized
                else requested_command if verification_kind
                else ""
            ),
        )

    return Tool(
        name="run_command",
        description=(
            "在工作目录下执行 shell 命令并返回 stdout/stderr 与退出码"
            f"（Windows 为 PowerShell，其余为 sh）。默认超时 {DEFAULT_TIMEOUT} 秒。"
            "不要执行需要交互输入或常驻不退出的命令（如 dev server）。"
            "修改文件优先使用 edit_file/write_file；shell 的净文件副作用会被审计并计入变更。"
            "验证改动请优先跑项目自带的测试命令。"
            "Windows 上 bare pytest 会自动改为当前 Python 的 -m pytest，避免解释器错位。"
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
        danger_check=lambda args: is_dangerous(str(args.get("command", ""))),
    )
