"""确定性假 Worker：用于后端测试和浏览器联调，绝不调用模型或文件工具。"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.worker.protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    ProtocolError,
    ProtocolWriter,
    read_message,
    utc_now,
)


def _event(writer: ProtocolWriter, task_id: str, kind: str, data: dict[str, Any]) -> None:
    writer.task_message("event", task_id, {"kind": kind, "data": data})


def _approval(
    writer: ProtocolWriter,
    task_id: str,
    *,
    name: str,
    args: dict[str, Any],
    high: bool = False,
) -> str:
    approval_id = str(uuid.uuid4())
    allowed = ["ALLOW_ONCE", "DENY"] if high else [
        "ALLOW_ONCE",
        "ALLOW_SESSION",
        "DENY",
    ]
    writer.task_message(
        "approval_required",
        task_id,
        {
            "approvalId": approval_id,
            "tool": {"name": name, "args": args},
            "riskLevel": "HIGH" if high else "NORMAL",
            "dangerReason": "确定性高风险测试请求" if high else None,
            "allowedDecisions": allowed,
            "requestedAt": utc_now(),
        },
    )
    response = read_message(sys.stdin)
    payload = response.get("payload")
    if response.get("type") != "approval_response" or not isinstance(payload, dict):
        raise ProtocolError("假 Worker 等待 approval_response。")
    if payload.get("taskId") != task_id or payload.get("approvalId") != approval_id:
        raise ProtocolError("假 Worker 收到不匹配的审批。")
    decision = payload.get("decision")
    if decision not in allowed:
        raise ProtocolError("假 Worker 收到当前风险不允许的决定。")
    writer.task_message(
        "approval_resolved",
        task_id,
        {
            "approvalId": approval_id,
            "decision": decision,
            "resolvedAt": utc_now(),
        },
    )
    return str(decision)


def _denied(writer: ProtocolWriter, task_id: str, changed: list[str]) -> int:
    outcome = {
        "success": False,
        "stopReason": "denied",
        "steps": 1,
        "totalTokens": 3820,
        "finalText": "用户拒绝了有副作用的操作，任务已停止。",
        "changedPaths": changed,
        "verification": [],
        "error": None,
    }
    _event(
        writer,
        task_id,
        "run_finished",
        {
            "reason": "denied",
            "steps": 1,
            "totalTokens": 3820,
            "changedPaths": changed,
            "verification": "",
        },
    )
    writer.task_message("result", task_id, outcome)
    return 0


def run() -> int:
    writer = ProtocolWriter(sys.stdout)
    writer.ready(os.getpid())
    start = read_message(sys.stdin)
    if start.get("type") != "start" or not isinstance(start.get("payload"), dict):
        raise ProtocolError("假 Worker 第一条输入必须是 start。")
    payload = start["payload"]
    task_id = str(payload.get("taskId", ""))
    prompt = str(payload.get("prompt", ""))
    workspace = str(payload.get("workspace", ""))
    max_steps = int(payload.get("maxSteps", 40))
    if not task_id:
        raise ProtocolError("假 Worker 缺少 taskId。")

    if "[fake:invalid-json]" in prompt:
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        time.sleep(0.1)
        return 5
    if "[fake:exit]" in prompt:
        return 6

    _event(
        writer,
        task_id,
        "run_started",
        {"task": prompt, "model": "deterministic-fake-worker", "cwd": workspace},
    )
    _event(writer, task_id, "turn_started", {"step": 1, "maxSteps": max_steps})
    _event(
        writer,
        task_id,
        "assistant_text",
        {"text": "先读取路由入口与现有测试，确认 Header 进入优先级判断前的形态。"},
    )
    _event(
        writer,
        task_id,
        "tool_call_started",
        {"callId": "fake-read", "name": "read_file", "args": {"path": "router/headers.py"}},
    )
    _event(
        writer,
        task_id,
        "tool_call_finished",
        {
            "callId": "fake-read",
            "name": "read_file",
            "ok": True,
            "summary": "router/headers.py 1-86",
            "detail": "读取成功；演示 Worker 不返回生产源码。",
            "durationMs": 4,
        },
    )
    _event(
        writer,
        task_id,
        "context_stats",
        {"usedTokens": 3820, "limit": 1000000, "messageCount": 7},
    )

    edit = _approval(
        writer,
        task_id,
        name="edit_file",
        args={
            "path": "router/headers.py",
            "old_text": "raw = headers.get(name)",
            "new_text": "raw = get_header_case_insensitive(headers, name)",
        },
        high="[fake:high-risk]" in prompt,
    )
    if edit == "DENY":
        return _denied(writer, task_id, [])

    _event(
        writer,
        task_id,
        "tool_call_started",
        {
            "callId": "fake-edit",
            "name": "edit_file",
            "args": {"path": "router/headers.py"},
        },
    )
    _event(
        writer,
        task_id,
        "tool_call_finished",
        {
            "callId": "fake-edit",
            "name": "edit_file",
            "ok": True,
            "summary": "已更新 router/headers.py（唯一匹配）",
            "detail": "演示修改完成。",
            "durationMs": 8,
        },
    )
    _event(writer, task_id, "turn_started", {"step": 2, "maxSteps": max_steps})
    _event(
        writer,
        task_id,
        "assistant_text",
        {"text": "修改已经落盘；按完成协议，最后一次修改之后还需要新的可执行验证。"},
    )
    test = _approval(
        writer,
        task_id,
        name="run_command",
        args={"command": "pytest -q"},
    )
    if test == "DENY":
        return _denied(writer, task_id, ["router/headers.py"])

    _event(
        writer,
        task_id,
        "tool_call_started",
        {"callId": "fake-test", "name": "run_command", "args": {"command": "pytest -q"}},
    )
    _event(
        writer,
        task_id,
        "tool_call_finished",
        {
            "callId": "fake-test",
            "name": "run_command",
            "ok": True,
            "summary": "4 passed in 0.08s",
            "detail": "4 passed in 0.08s",
            "durationMs": 941,
        },
    )
    _event(
        writer,
        task_id,
        "context_stats",
        {"usedTokens": 7210, "limit": 1000000, "messageCount": 13},
    )
    _event(
        writer,
        task_id,
        "assistant_text",
        {"text": "Header 查找已统一规范化，五级优先顺序未改变；完整测试通过。"},
    )
    outcome = {
        "success": True,
        "stopReason": "done_verified",
        "steps": 2,
        "totalTokens": 7210,
        "finalText": "Header 查找已统一规范化，五级优先顺序未改变；完整测试通过。",
        "changedPaths": ["router/headers.py"],
        "verification": [
            {
                "kind": "test",
                "command": "python -m pytest -q",
                "summary": "4 passed in 0.08s",
                "step": 2,
            }
        ],
        "error": None,
    }
    _event(
        writer,
        task_id,
        "run_finished",
        {
            "reason": "done_verified",
            "steps": 2,
            "totalTokens": 7210,
            "changedPaths": ["router/headers.py"],
            "verification": "4 passed in 0.08s",
        },
    )
    writer.task_message("result", task_id, outcome)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except ProtocolError as exc:
        print(f"[fake-worker] {exc}", file=sys.stderr)
        raise SystemExit(2) from None
