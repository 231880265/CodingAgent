"""确定性假 Worker：验证 Session/Run 协议，不调用模型或真实文件工具。"""

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
    ProtocolError,
    ProtocolWriter,
    read_message,
    utc_now,
)


def _event(
    writer: ProtocolWriter,
    session_id: str,
    run_id: str,
    kind: str,
    data: dict[str, Any],
) -> None:
    writer.run_message(
        "event",
        session_id,
        run_id,
        {"kind": kind, "data": data},
    )


def _approval(
    writer: ProtocolWriter,
    session_id: str,
    run_id: str,
    *,
    name: str,
    args: dict[str, Any],
    high: bool = False,
) -> str:
    approval_id = str(uuid.uuid4())
    allowed = (
        ["ALLOW_ONCE", "DENY"]
        if high
        else ["ALLOW_ONCE", "ALLOW_SESSION", "DENY"]
    )
    writer.run_message(
        "approval_required",
        session_id,
        run_id,
        {
            "approvalId": approval_id,
            "tool": {"name": name, "args": args},
            "riskLevel": "HIGH" if high else "NORMAL",
            "dangerReason": "确定性高风险测试请求" if high else None,
            "allowedDecisions": allowed,
            "requestedAt": utc_now(),
        },
    )
    while True:
        response = read_message(sys.stdin)
        payload = response.get("payload")
        if not isinstance(payload, dict):
            raise ProtocolError("假 Worker 等待结构化审批或取消。")
        if payload.get("sessionId") != session_id or payload.get("runId") != run_id:
            # 同一进程中的迟到消息按身份丢弃，不能污染当前 Run。
            continue
        if response.get("type") == "cancel_run":
            return "CANCELLED"
        if response.get("type") != "approval_response":
            raise ProtocolError("假 Worker 等待 approval_response 或 cancel_run。")
        if payload.get("approvalId") != approval_id:
            continue
        decision = payload.get("decision")
        if decision not in allowed:
            raise ProtocolError("假 Worker 收到当前风险不允许的决定。")
        writer.run_message(
            "approval_resolved",
            session_id,
            run_id,
            {
                "approvalId": approval_id,
                "decision": decision,
                "resolvedAt": utc_now(),
            },
        )
        return str(decision)


def _finish(
    writer: ProtocolWriter,
    session_id: str,
    run_id: str,
    *,
    stop_reason: str,
    final_text: str,
    changed: list[str],
    verification: list[dict[str, Any]],
    steps: int,
    total_tokens: int,
) -> None:
    success = stop_reason in {"done_read_only", "done_verified"}
    _event(
        writer,
        session_id,
        run_id,
        "run_finished",
        {
            "reason": stop_reason,
            "steps": steps,
            "totalTokens": total_tokens,
            "changedPaths": changed,
            "verification": verification[-1]["summary"] if verification else "",
        },
    )
    writer.run_message(
        "result",
        session_id,
        run_id,
        {
            "success": success,
            "stopReason": stop_reason,
            "steps": steps,
            "totalTokens": total_tokens,
            "finalText": final_text,
            "changedPaths": changed,
            "verification": verification,
            "error": None,
        },
    )


def _cancelled(
    writer: ProtocolWriter,
    session_id: str,
    run_id: str,
    changed: list[str],
) -> None:
    _finish(
        writer,
        session_id,
        run_id,
        stop_reason="cancelled",
        final_text="本轮已取消；已经落盘的文件修改会保留。",
        changed=changed,
        verification=[],
        steps=1,
        total_tokens=3820,
    )


def _run_goal(
    writer: ProtocolWriter,
    *,
    session_id: str,
    run_id: str,
    prompt: str,
    workspace: str,
    max_steps: int,
    run_number: int,
) -> None:
    if "[fake:invalid-json]" in prompt:
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
        time.sleep(0.1)
        raise SystemExit(5)
    if "[fake:exit]" in prompt:
        raise SystemExit(6)

    prefix = f"fake-r{run_number}"
    _event(
        writer,
        session_id,
        run_id,
        "run_started",
        {"task": prompt, "model": "deterministic-fake-worker", "cwd": workspace},
    )
    _event(writer, session_id, run_id, "turn_started", {"step": 1, "maxSteps": max_steps})
    _event(
        writer,
        session_id,
        run_id,
        "assistant_text",
        {"text": "先读取路由入口与现有测试，确认 Header 进入优先级判断前的形态。"},
    )
    _event(
        writer,
        session_id,
        run_id,
        "tool_call_started",
        {"callId": f"{prefix}-read", "name": "read_file", "args": {"path": "router/headers.py"}},
    )
    _event(
        writer,
        session_id,
        run_id,
        "tool_call_finished",
        {
            "callId": f"{prefix}-read",
            "name": "read_file",
            "ok": True,
            "summary": "router/headers.py 1-86",
            "detail": "读取成功；演示 Worker 不返回生产源码。",
            "durationMs": 4,
        },
    )

    edit = _approval(
        writer,
        session_id,
        run_id,
        name="edit_file",
        args={"path": "router/headers.py", "old_text": "raw", "new_text": "normalized"},
        high="[fake:high-risk]" in prompt,
    )
    if edit == "CANCELLED":
        _cancelled(writer, session_id, run_id, [])
        return
    if edit == "DENY":
        _event(
            writer,
            session_id,
            run_id,
            "assistant_text",
            {"text": "写入被拒绝，本轮保留调查结论且不改文件。"},
        )
        _finish(
            writer,
            session_id,
            run_id,
            stop_reason="done_read_only",
            final_text="写入被拒绝；已保留只读调查结论，工作区未修改。",
            changed=[],
            verification=[],
            steps=1,
            total_tokens=3820,
        )
        return

    _event(
        writer,
        session_id,
        run_id,
        "tool_call_started",
        {"callId": f"{prefix}-edit", "name": "edit_file", "args": {"path": "router/headers.py"}},
    )
    _event(
        writer,
        session_id,
        run_id,
        "tool_call_finished",
        {
            "callId": f"{prefix}-edit",
            "name": "edit_file",
            "ok": True,
            "summary": "已更新 router/headers.py（唯一匹配）",
            "detail": "演示修改完成。",
            "durationMs": 8,
            "touchedPaths": ["router/headers.py"],
            "createdPaths": [],
            "modifiedPaths": ["router/headers.py"],
            "deletedPaths": [],
            "derivedPaths": [],
            "verificationKind": "",
            "verificationCommand": "",
        },
    )
    _event(writer, session_id, run_id, "turn_started", {"step": 2, "maxSteps": max_steps})
    test = _approval(
        writer,
        session_id,
        run_id,
        name="run_command",
        args={"command": "pytest -q"},
    )
    if test == "CANCELLED":
        _cancelled(writer, session_id, run_id, ["router/headers.py"])
        return
    if test == "DENY":
        _finish(
            writer,
            session_id,
            run_id,
            stop_reason="done_unverified",
            final_text="验证命令被拒绝；修改已保留但不能标记为可信完成。",
            changed=["router/headers.py"],
            verification=[],
            steps=2,
            total_tokens=6210,
        )
        return

    _event(
        writer,
        session_id,
        run_id,
        "tool_call_started",
        {"callId": f"{prefix}-test", "name": "run_command", "args": {"command": "pytest -q"}},
    )
    _event(
        writer,
        session_id,
        run_id,
        "tool_call_finished",
        {
            "callId": f"{prefix}-test",
            "name": "run_command",
            "ok": True,
            "summary": "4 passed in 0.08s",
            "detail": "4 passed in 0.08s",
            "durationMs": 941,
            "touchedPaths": [],
            "createdPaths": [],
            "modifiedPaths": [],
            "deletedPaths": [],
            "derivedPaths": [],
            "verificationKind": "test",
            "verificationCommand": "python -m pytest -q",
        },
    )
    total_tokens = 7210 + (run_number - 1) * 2400
    final_text = (
        "Header 查找已统一规范化，五级优先顺序未改变；完整测试通过。"
        if run_number == 1
        else "已结合上一轮上下文完成后续检查；最终版本测试仍然通过。"
    )
    _event(writer, session_id, run_id, "assistant_text", {"text": final_text})
    _finish(
        writer,
        session_id,
        run_id,
        stop_reason="done_verified",
        final_text=final_text,
        changed=["router/headers.py"],
        verification=[
            {
                "kind": "test",
                "command": "python -m pytest -q",
                "summary": "4 passed in 0.08s",
                "step": 2,
            }
        ],
        steps=2,
        total_tokens=total_tokens,
    )


def _start(message: dict[str, Any]) -> tuple[str, str, str, str, int, int]:
    if message.get("type") != "start" or not isinstance(message.get("payload"), dict):
        raise ProtocolError("假 Worker 第一条输入必须是 start。")
    payload = message["payload"]
    session_id = str(payload.get("sessionId", ""))
    run_id = str(payload.get("runId", ""))
    prompt = str(payload.get("prompt", ""))
    workspace = str(payload.get("workspace", ""))
    max_steps = int(payload.get("maxSteps", 100))
    conversation = payload.get("conversation", [])
    if not session_id or not run_id or not prompt:
        raise ProtocolError("假 Worker 缺少 Session、Run 或 prompt。")
    if not isinstance(conversation, list) or len(conversation) % 2 != 0:
        raise ProtocolError("假 Worker 收到非法 Conversation 快照。")
    expected = "user"
    for item in conversation:
        if not isinstance(item, dict) or item.get("role") != expected:
            raise ProtocolError("假 Worker 收到乱序 Conversation 快照。")
        if not isinstance(item.get("content"), str) or not item["content"].strip():
            raise ProtocolError("假 Worker 收到空 Conversation 消息。")
        expected = "assistant" if expected == "user" else "user"
    return session_id, run_id, prompt, workspace, max_steps, len(conversation) // 2


def _follow_up(message: dict[str, Any], session_id: str) -> tuple[str, str, int]:
    if message.get("type") != "run" or not isinstance(message.get("payload"), dict):
        raise ProtocolError("假 Worker 完成 Run 后只接受 run。")
    payload = message["payload"]
    if payload.get("sessionId") != session_id:
        raise ProtocolError("假 Worker 收到不匹配的 sessionId。")
    run_id = payload.get("runId")
    prompt = payload.get("prompt")
    max_steps = payload.get("maxSteps", 100)
    if not isinstance(run_id, str) or not run_id:
        raise ProtocolError("假 Worker 的 run.runId 不能为空。")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProtocolError("假 Worker 的 run.prompt 不能为空。")
    if not isinstance(max_steps, int) or not 1 <= max_steps <= 100:
        raise ProtocolError("假 Worker 的 run.maxSteps 非法。")
    return run_id, prompt.strip(), max_steps


def run() -> int:
    writer = ProtocolWriter(sys.stdout)
    writer.ready(os.getpid())
    session_id, run_id, prompt, workspace, max_steps, restored_runs = _start(
        read_message(sys.stdin)
    )
    run_number = restored_runs + 1
    while True:
        _run_goal(
            writer,
            session_id=session_id,
            run_id=run_id,
            prompt=prompt,
            workspace=workspace,
            max_steps=max_steps,
            run_number=run_number,
        )
        run_id, prompt, max_steps = _follow_up(read_message(sys.stdin), session_id)
        run_number += 1


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except ProtocolError as exc:
        print(f"[fake-worker] {exc}", file=sys.stderr)
        raise SystemExit(2) from None
