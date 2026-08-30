"""把 Agent 的同步审批与协作式取消适配为 JSONL 输入。"""

from __future__ import annotations

import queue
import threading
import uuid
from typing import Any, TextIO

from hako.tools import Tool

from .protocol import ProtocolError, ProtocolWriter, read_message, utc_now

DECISIONS = {"ALLOW_ONCE", "ALLOW_SESSION", "DENY"}


class ApprovalInput:
    """唯一读取 Worker stdin 的线程，并按消息语义分流。

    Agent.run 是同步的，所以取消不能等主线程再次读取 stdin。后台线程收到
    cancel_run 后立即置位；run_command 会轮询该标记，审批等待也会被唤醒。
    """

    def __init__(self, stream: TextIO, *, session_id: str) -> None:
        self._stream = stream
        self._session_id = session_id
        self._approvals: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._goals: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._cancelled = threading.Event()
        self._state_lock = threading.Lock()
        self._active_run_id = ""
        self._thread = threading.Thread(
            target=self._read_forever,
            name="hako-worker-input",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def begin_run(self, run_id: str) -> None:
        with self._state_lock:
            self._active_run_id = run_id
            self._cancelled.clear()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def next_approval(self, run_id: str) -> dict[str, Any]:
        while True:
            item = self._approvals.get()
            if isinstance(item, BaseException):
                raise ProtocolError(str(item))
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("sessionId") != self._session_id:
                continue
            if payload.get("runId") != run_id:
                # 上一 Run 的迟到审批/取消不能污染当前 Run。
                continue
            return item

    def next_goal(self) -> dict[str, Any]:
        item = self._goals.get()
        if isinstance(item, BaseException):
            raise ProtocolError(str(item))
        return item

    def _read_forever(self) -> None:
        try:
            while True:
                message = read_message(self._stream)
                message_type = message.get("type")
                if message_type == "approval_response":
                    self._approvals.put(message)
                elif message_type == "run":
                    self._goals.put(message)
                elif message_type == "cancel_run":
                    payload = message.get("payload")
                    if not isinstance(payload, dict):
                        raise ProtocolError("cancel_run.payload 必须是对象。")
                    if payload.get("sessionId") != self._session_id:
                        continue
                    with self._state_lock:
                        active = self._active_run_id
                    if payload.get("runId") != active:
                        continue
                    self._cancelled.set()
                    self._approvals.put(message)
                else:
                    raise ProtocolError(
                        "Worker 运行期间只接受 approval_response、cancel_run 或 run。"
                    )
        except BaseException as exc:  # noqa: BLE001 - 跨线程传回主 Agent 线程
            self._approvals.put(exc)
            self._goals.put(exc)


class ApprovalCoordinator:
    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        writer: ProtocolWriter,
        incoming: ApprovalInput,
    ) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self.writer = writer
        self.incoming = incoming
        self.remembered_tools: set[str] = set()

    def begin_run(self, run_id: str) -> None:
        self.run_id = run_id
        self.incoming.begin_run(run_id)

    def __call__(self, tool: Tool, args: dict[str, Any]) -> bool:
        danger_reason = tool.danger_reason(args)
        if tool.name in self.remembered_tools and danger_reason is None:
            return True

        approval_id = str(uuid.uuid4())
        risk_level = "HIGH" if danger_reason else "NORMAL"
        allowed = (
            ["ALLOW_ONCE", "DENY"]
            if danger_reason
            else ["ALLOW_ONCE", "ALLOW_SESSION", "DENY"]
        )
        self.writer.run_message(
            "approval_required",
            self.session_id,
            self.run_id,
            {
                "approvalId": approval_id,
                "tool": {"name": tool.name, "args": args},
                "riskLevel": risk_level,
                "dangerReason": danger_reason,
                "allowedDecisions": allowed,
                "requestedAt": utc_now(),
            },
        )

        message = self.incoming.next_approval(self.run_id)
        if message.get("type") == "cancel_run":
            return False
        if message.get("type") != "approval_response":
            raise ProtocolError("等待审批时只接受 approval_response 或 cancel_run。")
        payload = message.get("payload")
        if not isinstance(payload, dict):
            raise ProtocolError("approval_response.payload 必须是对象。")
        if payload.get("sessionId") != self.session_id:
            raise ProtocolError("approval_response.sessionId 不匹配。")
        if payload.get("runId") != self.run_id:
            raise ProtocolError("approval_response.runId 不匹配。")
        if payload.get("approvalId") != approval_id:
            raise ProtocolError("approval_response.approvalId 不是当前审批。")
        decision = payload.get("decision")
        if decision not in DECISIONS:
            raise ProtocolError("approval_response.decision 非法。")
        if decision not in allowed:
            raise ProtocolError("当前风险等级不允许该审批决定。")

        if decision == "ALLOW_SESSION":
            self.remembered_tools.add(tool.name)
        self.writer.run_message(
            "approval_resolved",
            self.session_id,
            self.run_id,
            {
                "approvalId": approval_id,
                "decision": decision,
                "resolvedAt": utc_now(),
            },
        )
        return decision != "DENY"
