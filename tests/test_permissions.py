"""分级审批：便捷放行不能吞掉高风险边界。"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from hako.tools.shell import make_run_command
from hako.ui import renderer


def _console() -> tuple[Console, StringIO]:
    stream = StringIO()
    return Console(file=stream, force_terminal=False, color_system=None), stream


def test_headless_auto_approve_allows_ordinary_but_denies_dangerous(
    monkeypatch, workspace: Path
):
    console, stream = _console()
    monkeypatch.setattr(renderer, "stdin_is_tty", lambda: False)
    approve = renderer.make_approval_fn(console, auto_approve=True)
    tool = make_run_command(workspace)

    assert approve(tool, {"command": "pytest -q"})
    assert not approve(tool, {"command": "git reset --hard HEAD"})
    assert "非交互环境拒绝高风险" in stream.getvalue()


def test_headless_without_y_denies_even_ordinary_mutating_tool(
    monkeypatch, workspace: Path
):
    console, stream = _console()
    monkeypatch.setattr(renderer, "stdin_is_tty", lambda: False)
    approve = renderer.make_approval_fn(console, auto_approve=False)

    assert not approve(make_run_command(workspace), {"command": "pytest -q"})
    assert "显式使用 -y" in stream.getvalue()


def test_remembered_run_command_does_not_cover_later_dangerous_call(
    monkeypatch, workspace: Path
):
    console, _ = _console()
    choices: list[tuple[str, ...]] = []

    def choose(console, prompt, options):
        choices.append(tuple(options))
        return 1  # 普通命令：记住；危险命令：拒绝

    monkeypatch.setattr(renderer, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(renderer, "select", choose)
    approve = renderer.make_approval_fn(console)
    tool = make_run_command(workspace)

    assert approve(tool, {"command": "pytest -q"})
    assert approve(tool, {"command": "ruff check ."})  # 已记住，不再询问
    assert not approve(tool, {"command": "git push origin main"})
    assert len(choices) == 2
    assert choices[0] == (
        "允许这一次",
        "允许本次会话内所有 run_command",
        "拒绝",
    )
    assert choices[1] == ("明确允许这一次", "拒绝")


def test_y_still_requires_one_time_confirmation_for_dangerous_call(
    monkeypatch, workspace: Path
):
    console, _ = _console()
    calls = 0

    def choose(console, prompt, options):
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(renderer, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(renderer, "select", choose)
    approve = renderer.make_approval_fn(console, auto_approve=True)
    tool = make_run_command(workspace)

    assert approve(tool, {"command": "pytest -q"})
    assert approve(tool, {"command": "Remove-Item target -Force -Recurse"})
    assert calls == 1
