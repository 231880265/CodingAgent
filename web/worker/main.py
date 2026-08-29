"""真实 hako Web Worker：JSONL 薄适配，不复制 Agent 主循环。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hako.config import Config  # noqa: E402
from hako.events import EventBus  # noqa: E402
from hako.llm import LLMClient  # noqa: E402
from hako.loop import Agent  # noqa: E402
from hako.subagent import make_delegate_readonly  # noqa: E402
from hako.tools import build_default_registry  # noqa: E402
from web.worker.approval import ApprovalCoordinator, ApprovalInput  # noqa: E402
from web.worker.protocol import (  # noqa: E402
    ProtocolError,
    ProtocolWriter,
    read_message,
    redact,
    result_payload,
)


def build_web_agent(
    *,
    workspace: Path,
    max_steps: int,
    bus: EventBus,
    approve: ApprovalCoordinator,
) -> Agent:
    config = Config.from_env(workspace=workspace)
    config.max_steps = max_steps
    extra_tools = (
        [make_delegate_readonly(config, bus)] if config.enable_subagent else []
    )
    registry = build_default_registry(
        config.workspace,
        config.tool_result_budget,
        extra_tools=extra_tools,
    )
    return Agent(
        config=config,
        registry=registry,
        client=LLMClient(
            config.api_key,
            config.base_url,
            config.model,
            max_output_tokens=config.max_output_tokens,
            enable_thinking=config.enable_thinking,
        ),
        bus=bus,
        approve=approve,
    )


def _start_payload(message: dict) -> tuple[str, Path, str, int]:
    if message.get("type") != "start":
        raise ProtocolError("Worker 第一条输入必须是 start。")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ProtocolError("start.payload 必须是对象。")
    task_id = payload.get("taskId")
    prompt = payload.get("prompt")
    workspace_raw = payload.get("workspace")
    max_steps = payload.get("maxSteps", 40)
    if not isinstance(task_id, str) or not task_id:
        raise ProtocolError("start.taskId 必须是非空字符串。")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProtocolError("start.prompt 必须是非空字符串。")
    if not isinstance(workspace_raw, str):
        raise ProtocolError("start.workspace 必须是字符串。")
    workspace = Path(workspace_raw)
    if not workspace.is_absolute():
        raise ProtocolError("start.workspace 必须是绝对路径。")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ProtocolError("start.workspace 必须是目录。")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or not 1 <= max_steps <= 100:
        raise ProtocolError("start.maxSteps 必须在 1 到 100 之间。")
    return task_id, workspace, prompt.strip(), max_steps


def run() -> int:
    wire_stdout = sys.stdout
    sys.stdout = sys.stderr
    writer = ProtocolWriter(wire_stdout)
    task_id = ""
    try:
        writer.ready(os.getpid())
        start = read_message(sys.stdin)
        task_id, workspace, prompt, max_steps = _start_payload(start)

        incoming = ApprovalInput(sys.stdin)
        incoming.start()
        bus = EventBus()
        bus.subscribe(lambda event: writer.event(task_id, event))
        approve = ApprovalCoordinator(
            task_id=task_id,
            writer=writer,
            incoming=incoming,
        )
        agent = build_web_agent(
            workspace=workspace,
            max_steps=max_steps,
            bus=bus,
            approve=approve,
        )
        result = agent.run(prompt)
        writer.task_message("result", task_id, result_payload(result))
        return 0
    except ProtocolError as exc:
        if task_id:
            writer.task_message(
                "fatal",
                task_id,
                {"code": "WORKER_PROTOCOL_ERROR", "message": redact(str(exc))},
            )
        else:
            print(f"[worker] protocol error: {redact(str(exc))}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        message = redact(str(exc)) or "无法构造 Agent。"
        if task_id:
            writer.task_message(
                "fatal",
                task_id,
                {"code": "AGENT_BUILD_FAILED", "message": message},
            )
        else:
            print(f"[worker] agent build failed: {message}", file=sys.stderr)
        return 3
    except BaseException as exc:  # noqa: BLE001 - Worker 顶层故障必须结构化收口
        message = redact(f"{type(exc).__name__}: {exc}")
        if task_id:
            writer.task_message(
                "fatal",
                task_id,
                {"code": "WORKER_ERROR", "message": message},
            )
        else:
            print(f"[worker] fatal: {message}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(run())
