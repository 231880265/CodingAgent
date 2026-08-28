"""CLI 入口。

单次执行：  python main.py "把 README 里的拼写错误改掉"
交互模式：  python main.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.text import Text

from hako.config import Config
from hako.events import EventBus
from hako.llm import LLMClient
from hako.loop import Agent
from hako.subagent import make_delegate_readonly
from hako.tools import build_default_registry
from hako.ui import Renderer, make_approval_fn, setup_console


def build_agent(args: argparse.Namespace, console: Console) -> Agent:
    config = Config.from_env(workspace=Path(args.workspace))
    if args.max_steps:
        config.max_steps = args.max_steps

    bus = EventBus()
    bus.subscribe(Renderer(console, verbose=args.verbose).handle)
    extra_tools = (
        [make_delegate_readonly(config, bus)] if config.enable_subagent else []
    )

    return Agent(
        config=config,
        registry=build_default_registry(
            config.workspace,
            config.tool_result_budget,
            extra_tools=extra_tools,
        ),
        client=LLMClient(
            config.api_key,
            config.base_url,
            config.model,
            max_output_tokens=config.max_output_tokens,
            enable_thinking=config.enable_thinking,
        ),
        bus=bus,
        approve=make_approval_fn(console, auto_approve=args.yes),
    )


def interactive(agent: Agent, console: Console) -> int:
    """交互模式：多轮任务共用同一个 agent，因此也共用同一份对话历史。"""
    console.print()
    console.print(Text("  hako 交互模式  ·  输入任务，Ctrl-C 或 /exit 退出", style="dim"))

    while True:
        console.print()
        try:
            task = console.input("[bold cyan]  › [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0

        if not task:
            continue
        if task in ("/exit", "/quit"):
            return 0

        try:
            agent.run(task)
        except KeyboardInterrupt:
            # 优雅中断：历史保留，用户可以接着提下一个任务
            console.print()
            console.print(Text("  ⊘ 已中断（历史保留，可继续输入任务）", style="yellow"))


def main() -> int:
    # 必须在任何输出之前，否则第一行就已经是乱码了
    setup_console()

    parser = argparse.ArgumentParser(prog="hako", description="一个从零手写的编程 agent")
    parser.add_argument("task", nargs="?", help="要完成的任务；留空进入交互模式")
    parser.add_argument("-C", "--workspace", default=".", help="工作目录，默认当前目录")
    parser.add_argument("-v", "--verbose", action="store_true", help="展开工具结果与上下文占用")
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="自动批准普通写入与命令；高风险命令仍需逐次确认",
    )
    parser.add_argument("--max-steps", type=int, help="覆盖步数上限")
    args = parser.parse_args()

    console = Console()

    try:
        agent = build_agent(args, console)
    except SystemExit as exc:                 # Config.from_env 缺 key 时抛
        console.print(Text(f"\n  {exc}\n", style="bold red"))
        return 1

    if args.task:
        result = agent.run(args.task)
        return 0 if result.ok else 1

    return interactive(agent, console)


if __name__ == "__main__":
    sys.exit(main())
