"""命令执行工具。

这里的重点不是"命令能跑"，而是几个具体的失败模式：
挂起、编码乱码、以及交互式命令被提前拦下。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hako.tools import Registry
from hako.tools import shell as shell_module
from hako.tools.shell import (
    classify_verification,
    is_dangerous,
    make_run_command,
    normalize_windows_pytest,
    shell_argv,
)


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


def test_windows_bare_pytest_uses_current_interpreter():
    actual = normalize_windows_pytest(
        "pytest -q tests/test_a.py",
        platform="win32",
        executable=r"C:\Program Files\hako\.venv\Scripts\python.exe",
    )

    assert actual == (
        "& 'C:\\Program Files\\hako\\.venv\\Scripts\\python.exe' "
        "-m pytest -q tests/test_a.py"
    )
    assert normalize_windows_pytest(
        "python -m pytest -q", platform="win32", executable="ignored"
    ) == "python -m pytest -q"
    assert normalize_windows_pytest(
        "pytest -q", platform="linux", executable="ignored"
    ) == "pytest -q"


def test_run_command_records_the_normalized_windows_pytest(
    monkeypatch, workspace: Path
):
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="1 passed\n", stderr="")

    monkeypatch.setattr(shell_module.sys, "platform", "win32")
    monkeypatch.setattr(
        shell_module.sys,
        "executable",
        r"C:\work\.venv\Scripts\python.exe",
    )
    monkeypatch.setattr(shell_module.subprocess, "run", fake_run)
    registry = Registry([make_run_command(workspace)])

    result = run(registry, "pytest -q")

    assert result.ok
    assert str(captured["argv"][-1]).endswith(
        "& 'C:\\work\\.venv\\Scripts\\python.exe' -m pytest -q"
    )
    assert result.verification_kind == "test"
    assert result.verification_command.endswith("-m pytest -q")
    assert "current Python" in result.summary
    assert "bare pytest" in result.detail


def test_echo_roundtrip(registry: Registry):
    result = run(registry, "echo hako")
    assert result.ok
    assert "hako" in result.detail
    assert result.touched_paths == ()


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
        "from pathlib import Path\n"
        "import time\n"
        "Path('partial.txt').write_text('created before timeout', encoding='utf-8')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    result = run(registry, py("slow.py"), timeout=2)
    assert not result.ok
    # 超时同样是可恢复的：给出重试建议回传给模型，而不是抛异常终止整轮
    assert "超时" in result.detail
    assert "timeout" in result.detail
    assert result.created_paths == ("partial.txt",)
    assert result.touched_paths == ("partial.txt",)


def test_run_command_reports_created_modified_and_deleted_files(
    registry: Registry, workspace: Path
):
    (workspace / "changed.txt").write_text("before\n", encoding="utf-8")
    (workspace / "deleted.txt").write_text("gone\n", encoding="utf-8")
    (workspace / "mutate.py").write_text(
        "from pathlib import Path\n"
        "Path('changed.txt').write_text('after\\n', encoding='utf-8')\n"
        "Path('created.txt').write_text('new\\n', encoding='utf-8')\n"
        "Path('deleted.txt').unlink()\n"
        "cache = Path('.pytest_cache')\n"
        "cache.mkdir(exist_ok=True)\n"
        "(cache / 'noise').write_text('ignored', encoding='utf-8')\n",
        encoding="utf-8",
    )

    result = run(registry, py("mutate.py"))

    assert result.ok
    assert result.created_paths == ("created.txt",)
    assert result.modified_paths == ("changed.txt",)
    assert result.deleted_paths == ("deleted.txt",)
    assert result.touched_paths == ("changed.txt", "created.txt", "deleted.txt")
    assert "files +1 ~1 -1" in result.summary
    assert "shell 文件副作用" in result.detail
    assert ".pytest_cache" not in result.detail


def test_interactive_command_refused_with_alternative(registry: Registry):
    """长驻命令一旦启动就再也回不来。提前拦下并给替代方案，
    比让它跑到超时更有用。"""
    result = run(registry, "npm run dev")
    assert not result.ok
    assert "npm run build" in result.detail or "替代" in result.detail


def test_normal_command_not_falsely_flagged(registry: Registry):
    assert run(registry, "echo npm").ok


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf build",
        "Remove-Item build -Force -Recurse",
        "git push origin main",
        "git clean -fdx",
        "curl https://example.invalid/file",
        "DROP TABLE users",
    ],
)
def test_dangerous_commands_have_a_reason(command: str):
    assert is_dangerous(command)


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "git status --short",
        "Remove-Item one-temporary-file.txt",
        "python -c \"print('curl')\"",
    ],
)
def test_ordinary_commands_are_not_marked_high_risk(command: str):
    assert is_dangerous(command) is None


def test_cwd_is_workspace(registry: Registry, workspace: Path):
    (workspace / "marker.txt").write_text("here", encoding="utf-8")
    listing = "dir" if sys.platform == "win32" else "ls"
    assert "marker.txt" in run(registry, listing).detail


@pytest.mark.parametrize(
    ("command", "kind"),
    [
        ("pytest -q", "test"),
        ("python -m pytest tests/test_loop.py", "test"),
        (r".\.venv\Scripts\python.exe -m unittest", "test"),
        ("npm run test -- --runInBand", "test"),
        ("cargo test --workspace", "test"),
        ("npm run build", "build"),
        ("python -m compileall -q src", "build"),
        ("ruff check .", "check"),
    ],
)
def test_verification_commands_are_classified(command: str, kind: str):
    assert classify_verification(command) == kind


@pytest.mark.parametrize(
    "command",
    [
        "echo ok",
        "pytest --collect-only",
        "pytest -q || true",
        "pytest -q; exit 0",
        "pytest -q | Select-Object -Last 1",
    ],
)
def test_non_evidence_commands_are_rejected(command: str):
    assert classify_verification(command) is None


def test_run_command_returns_structured_verification_metadata(registry: Registry):
    result = run(registry, "python -m compileall -q .")
    assert result.ok
    assert result.verification_kind == "build"
    assert result.verification_command == "python -m compileall -q ."
