"""命令执行工具。

这里的重点不是"命令能跑"，而是几个具体的失败模式：
挂起、编码乱码、以及交互式命令被提前拦下。
"""

from __future__ import annotations

import sys
from pathlib import Path

from hako.tools import Registry
from hako.tools.shell import shell_argv


def run(registry: Registry, command: str, **kw):
    return registry.invoke("run_command", {"command": command, **kw})


def py(script: str) -> str:
    """跑一个脚本文件的命令串。PowerShell 里执行带引号的可执行路径需要 & 调用运算符。"""
    prefix = "& " if sys.platform == "win32" else ""
    return f'{prefix}"{sys.executable}" {script}'


def test_shell_argv_keeps_command_as_single_element():
    """不用 shell=True：模型给的整条命令是 argv 里的一个元素，
    不参与 argv 拼接，也就没有引号逃逸问题。"""
    argv = shell_argv("echo hi && echo there")
    # 整条命令落在最后一个元素里（Windows 上前面还有个 UTF-8 前缀），
    # 不会被切成多个 argv 项 —— 也就没有 Python/shell 两层转义叠加的问题。
    assert argv[-1].endswith("echo hi && echo there")
    assert len(argv) >= 3
    assert not any("echo there" == part for part in argv[:-1])


def test_echo_roundtrip(registry: Registry):
    result = run(registry, "echo hako")
    assert result.ok
    assert "hako" in result.detail


def test_exit_code_reported(registry: Registry):
    """非零退出不是崩溃，是给模型的信息——它得知道命令失败了。"""
    code = "exit 3"
    result = run(registry, code)
    assert "exit=3" in result.detail


def test_utf8_output_not_mojibake(registry: Registry, workspace: Path):
    """Windows 默认 cp936，不强制 UTF-8 的话中文输出全是乱码，
    模型看到乱码只会瞎猜。"""
    (workspace / "p.py").write_text(
        "print('中文输出正常')\n", encoding="utf-8"
    )
    result = run(registry, py("p.py"))
    assert "中文输出正常" in result.detail


def test_stdin_is_closed_not_hanging(registry: Registry, workspace: Path):
    """stdin=DEVNULL 的理由：等输入的命令应当立刻失败，
    而不是挂到 timeout 白烧 60 秒。"""
    (workspace / "ask.py").write_text("input('name: ')\n", encoding="utf-8")
    result = run(registry, py("ask.py"), timeout=15)
    assert "exit=0" not in result.detail
    assert "EOF" in result.detail


def test_timeout_is_reported_as_recoverable(registry: Registry, workspace: Path):
    (workspace / "slow.py").write_text(
        "import time; time.sleep(30)\n", encoding="utf-8"
    )
    result = run(registry, py("slow.py"), timeout=2)
    assert not result.ok
    # 超时同样是可恢复的：给出重试建议回传给模型，而不是抛异常终止整轮
    assert "超时" in result.detail
    assert "timeout" in result.detail


def test_interactive_command_refused_with_alternative(registry: Registry):
    """长驻命令一旦启动就再也回不来。提前拦下并给替代方案，
    比让它跑到超时更有用。"""
    result = run(registry, "npm run dev")
    assert not result.ok
    assert "npm run build" in result.detail or "替代" in result.detail


def test_normal_command_not_falsely_flagged(registry: Registry):
    assert run(registry, "echo npm").ok


def test_cwd_is_workspace(registry: Registry, workspace: Path):
    (workspace / "marker.txt").write_text("here", encoding="utf-8")
    listing = "dir" if sys.platform == "win32" else "ls"
    assert "marker.txt" in run(registry, listing).detail
