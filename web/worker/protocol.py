"""hako Web Worker v1 JSONL 协议的确定性编解码。"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from hako import events as ev
from hako.loop import RunResult

PROTOCOL_VERSION = "1.0"
MAX_LINE_BYTES = 1024 * 1024

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
)

_EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "run_started": ("task", "model", "cwd"),
    "turn_started": ("step", "max_steps"),
    "assistant_text": ("text",),
    "tool_call_started": ("call_id", "name", "args"),
    "tool_call_finished": (
        "call_id",
        "name",
        "ok",
        "summary",
        "detail",
        "duration_ms",
    ),
    "context_stats": ("used_tokens", "limit", "message_count"),
    "verification_required": ("changed_paths", "message"),
    "continuation_required": (
        "attempt",
        "max_attempts",
        "finish_reason",
        "message",
    ),
    "subagent_started": ("task", "max_steps"),
    "subagent_finished": (
        "ok",
        "reason",
        "steps",
        "total_tokens",
        "max_context_tokens",
    ),
    "run_finished": (
        "reason",
        "steps",
        "total_tokens",
        "changed_paths",
        "verification",
    ),
    "agent_error": ("message", "fatal"),
}


class ProtocolError(RuntimeError):
    """输入或输出违反 Worker v1 契约。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def redact(value: str) -> str:
    sanitized = value
    for pattern in _SECRET_PATTERNS:
        replacement = r"\1[REDACTED]" if pattern.groups else "[REDACTED]"
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def _camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if is_dataclass(value):
        return {
            _camel(field.name): _json_value(getattr(value, field.name))
            for field in fields(value)
            if field.name != "kind"
        }
    return value


def event_payload(event: ev.Event) -> tuple[str, dict[str, Any]]:
    kind = event.kind
    names = _EVENT_FIELDS.get(kind)
    if names is None:
        raise ProtocolError(f"未映射的 hako 事件类型：{kind}")
    data = {_camel(name): _json_value(getattr(event, name)) for name in names}
    return kind, data


def result_payload(result: RunResult) -> dict[str, Any]:
    return {
        "success": result.ok,
        "stopReason": result.reason.value,
        "steps": result.steps,
        "totalTokens": result.total_tokens,
        "finalText": result.final_text,
        "changedPaths": list(result.changed_paths),
        "verification": [
            {
                "kind": item.kind,
                "command": item.command,
                "summary": item.summary,
                "step": item.step,
            }
            for item in result.verification
        ],
        "error": None,
    }


def read_message(stream: TextIO) -> dict[str, Any]:
    line = stream.readline(MAX_LINE_BYTES + 1)
    if line == "":
        raise ProtocolError("Worker 输入在消息完成前关闭。")
    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        raise ProtocolError("Worker 输入行超过 1 MiB。")
    if not line.endswith("\n"):
        raise ProtocolError("Worker 输入必须以 LF 结束。")
    if not line.strip():
        raise ProtocolError("Worker 输入不允许空行。")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Worker 输入不是合法 JSON：{exc.msg}") from None
    if not isinstance(message, dict):
        raise ProtocolError("Worker 顶层消息必须是 JSON 对象。")
    if message.get("protocolVersion") != PROTOCOL_VERSION:
        raise ProtocolError("Worker protocolVersion 不兼容。")
    if not isinstance(message.get("type"), str):
        raise ProtocolError("Worker 消息缺少字符串 type。")
    return message


class ProtocolWriter:
    """唯一可写 stdout 的组件，并串行分配任务级 sequence。"""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self._sequence = 0

    def ready(self, worker_pid: int) -> None:
        self._write(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "type": "ready",
                "workerPid": worker_pid,
                "capabilities": ["events", "approval", "run_result"],
            }
        )

    def task_message(
        self,
        message_type: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        with self._lock:
            self._sequence += 1
            message = {
                "protocolVersion": PROTOCOL_VERSION,
                "type": message_type,
                "taskId": task_id,
                "sequence": self._sequence,
                "occurredAt": utc_now(),
                "payload": _json_value(payload),
            }
            self._write_unlocked(message)

    def event(self, task_id: str, event: ev.Event) -> None:
        kind, data = event_payload(event)
        self.task_message("event", task_id, {"kind": kind, "data": data})

    def _write(self, message: dict[str, Any]) -> None:
        with self._lock:
            self._write_unlocked(message)

    def _write_unlocked(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_LINE_BYTES:
            raise ProtocolError("Worker 输出行超过 1 MiB。")
        self._stream.write(encoded + "\n")
        self._stream.flush()
