"""把 Agent 的同步审批函数适配为 JSONL 请求/响应。"""

from __future__ import annotations

import queue
import threading
import uuid
from typing import Any, TextIO

from hako.tools import Tool

from .protocol import ProtocolError, ProtocolWriter, read_message, utc_now

DECISIONS = {"ALLOW_ONCE", "ALLOW_SESSION", "DENY"}


class ApprovalInput:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._thread = threading.Thread(
            target=self._read_forever,
            name="hako-worker-input",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def next(self) -> dict[str, Any]:
        item = self._messages.get()
        if isinstance(item, BaseException):
            raise ProtocolError(str(item))
        return item

    def _read_forever(self) -> None:
        try:
            while True:
                self._messages.put(read_message(self._stream))
        except BaseException as exc:  # noqa: BLE001 - 跨线程传回主 Agent 线程
            self._messages.put(exc)


class ApprovalCoordinator:
    def __init__(
        self,
        *,
        task_id: str,
        writer: ProtocolWriter,
        incoming: ApprovalInput,
    ) -> None:
        self.task_id = task_id
        self.writer = writer
        self.incoming = incoming
        self.remembered_tools: set[str] = set()

    def __call__(self, tool: Tool, args: dict[str, Any]) -> bool:
        danger_reason = tool.danger_reason(args)
        if tool.name in self.remembered_tools and danger_reason is None:
            return True

        approval_id = str(uuid.uuid4())
        risk_level = "HIGH" if danger_reason else "NORMAL"
        allowed = ["ALLOW_ONCE", "DENY"] if danger_reason else [
            "ALLOW_ONCE",
            "ALLOW_SESSION",
            "DENY",
        ]
        self.writer.task_message(
            "approval_required",
            self.task_id,
            {
                "approvalId": approval_id,
                "tool": {"name": tool.name, "args": args},
                "riskLevel": risk_level,
                "dangerReason": danger_reason,
                "allowedDecisions": allowed,
                "requestedAt": utc_now(),
            },
        )

        message = self.incoming.next()
        if message.get("type") != "approval_response":
            raise ProtocolError("等待审批时只接受 approval_response。")
        payload = message.get("payload")
        if not isinstance(payload, dict):
            raise ProtocolError("approval_response.payload 必须是对象。")
        if payload.get("taskId") != self.task_id:
            raise ProtocolError("approval_response.taskId 不匹配。")
        if payload.get("approvalId") != approval_id:
            raise ProtocolError("approval_response.approvalId 不是当前审批。")
        decision = payload.get("decision")
        if decision not in DECISIONS:
            raise ProtocolError("approval_response.decision 非法。")
        if decision not in allowed:
            raise ProtocolError("当前风险等级不允许该审批决定。")

        if decision == "ALLOW_SESSION":
            self.remembered_tools.add(tool.name)
        self.writer.task_message(
            "approval_resolved",
            self.task_id,
            {
                "approvalId": approval_id,
                "decision": decision,
                "resolvedAt": utc_now(),
            },
        )
        return decision != "DENY"
