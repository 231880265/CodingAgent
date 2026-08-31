"""真实 hako Web Worker：JSONL 薄适配，不复制 Agent 主循环。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

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
    cancelled: Callable[[], bool],
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
        cancelled=cancelled,
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
        cancelled=cancelled,
    )


def _attachment_context(payload: dict) -> str:
    raw = payload.get("attachments", [])
    if not isinstance(raw, list):
        raise ProtocolError("attachments 必须是数组。")
    blocks: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ProtocolError("attachment 必须是对象。")
        name = item.get("name")
        media_type = item.get("mediaType")
        content = item.get("content")
        if not all(isinstance(value, str) and value for value in (name, media_type, content)):
            raise ProtocolError("attachment 缺少 name、mediaType 或 content。")
        blocks.append(
            f"<attachment name={name!r} media_type={media_type!r}>\n"
            f"{content}\n</attachment>"
        )
    return "\n\n".join(blocks)


def _semantic_history(payload: dict) -> list[dict[str, str]]:
    raw = payload.get("conversation", [])
    if not isinstance(raw, list) or len(raw) > 200:
        raise ProtocolError("start.conversation 必须是不超过 200 项的数组。")
    messages: list[dict[str, str]] = []
    expected = "user"
    for item in raw:
        if not isinstance(item, dict):
            raise ProtocolError("start.conversation 的消息必须是对象。")
        role = item.get("role")
        content = item.get("content")
        if role != expected or not isinstance(content, str) or not content.strip():
            raise ProtocolError("start.conversation 必须是非空 user/assistant 交替消息。")
        messages.append({"role": role, "content": content})
        expected = "assistant" if role == "user" else "user"
    if expected == "assistant":
        raise ProtocolError("start.conversation 不能以未回答的 user 消息结束。")
    return messages


def _start_payload(
    message: dict,
) -> tuple[str, str, Path, str, int, str, list[dict[str, str]]]:
    if message.get("type") != "start":
        raise ProtocolError("Worker 第一条输入必须是 start。")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ProtocolError("start.payload 必须是对象。")
    session_id = payload.get("sessionId")
    run_id = payload.get("runId")
    prompt = payload.get("prompt")
    workspace_raw = payload.get("workspace")
    max_steps = payload.get("maxSteps", 100)
    if not isinstance(session_id, str) or not session_id:
        raise ProtocolError("start.sessionId 必须是非空字符串。")
    if not isinstance(run_id, str) or not run_id:
        raise ProtocolError("start.runId 必须是非空字符串。")
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
    return (
        session_id,
        run_id,
        workspace,
        prompt.strip(),
        max_steps,
        _attachment_context(payload),
        _semantic_history(payload),
    )


def _run_payload(message: dict, session_id: str) -> tuple[str, str, int, str]:
    if message.get("type") != "run":
        raise ProtocolError("后续输入必须是 run。")
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ProtocolError("run.payload 必须是对象。")
    if payload.get("sessionId") != session_id:
        raise ProtocolError("run.sessionId 与当前 Session 不匹配。")
    run_id = payload.get("runId")
    if not isinstance(run_id, str) or not run_id:
        raise ProtocolError("run.runId 必须是非空字符串。")
    prompt = payload.get("prompt")
    max_steps = payload.get("maxSteps", 100)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProtocolError("run.prompt 必须是非空字符串。")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or not 1 <= max_steps <= 100:
        raise ProtocolError("run.maxSteps 必须在 1 到 100 之间。")
    return run_id, prompt.strip(), max_steps, _attachment_context(payload)


def run() -> int:
    wire_stdout = sys.stdout
    sys.stdout = sys.stderr
    writer = ProtocolWriter(wire_stdout)
    session_id = ""
    run_id = ""
    try:
        writer.ready(os.getpid())
        start = read_message(sys.stdin)
        (
            session_id,
            run_id,
            workspace,
            prompt,
            max_steps,
            attachments,
            semantic_history,
        ) = _start_payload(start)

        incoming = ApprovalInput(sys.stdin, session_id=session_id)
        incoming.begin_run(run_id)
        incoming.start()
        bus = EventBus()
        active_run = [run_id]
        bus.subscribe(lambda event: writer.event(session_id, active_run[0], event))
        approve = ApprovalCoordinator(
            session_id=session_id,
            run_id=run_id,
            writer=writer,
            incoming=incoming,
        )
        agent = build_web_agent(
            workspace=workspace,
            max_steps=max_steps,
            bus=bus,
            approve=approve,
            cancelled=incoming.is_cancelled,
        )
        try:
            agent.conversation.restore_semantic(semantic_history)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc
        while True:
            agent.config.max_steps = max_steps
            active_run[0] = run_id
            approve.begin_run(run_id)
            result = agent.run(prompt, attachment_context=attachments)
            writer.run_message("result", session_id, run_id, result_payload(result))
            follow_up = incoming.next_goal()
            run_id, prompt, max_steps, attachments = _run_payload(follow_up, session_id)
    except ProtocolError as exc:
        if session_id and run_id:
            writer.run_message(
                "fatal",
                session_id,
                run_id,
                {"code": "WORKER_PROTOCOL_ERROR", "message": redact(str(exc))},
            )
        else:
            print(f"[worker] protocol error: {redact(str(exc))}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        message = redact(str(exc)) or "无法构造 Agent。"
        if session_id and run_id:
            writer.run_message(
                "fatal",
                session_id,
                run_id,
                {"code": "AGENT_BUILD_FAILED", "message": message},
            )
        else:
            print(f"[worker] agent build failed: {message}", file=sys.stderr)
        return 3
    except BaseException as exc:  # noqa: BLE001 - Worker 顶层故障必须结构化收口
        message = redact(f"{type(exc).__name__}: {exc}")
        if session_id and run_id:
            writer.run_message(
                "fatal",
                session_id,
                run_id,
                {"code": "WORKER_ERROR", "message": message},
            )
        else:
            print(f"[worker] fatal: {message}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(run())
