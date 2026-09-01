"""Single-Run Working Memory compaction.

The durable Conversation and event log remain authoritative.  This module only
builds a smaller, protocol-valid view for the next model request when one Run
approaches its context budget.  Repository Memory and Verified Finish are
deliberately outside this module.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .history import Conversation, Turn
from .llm import LLMClient, estimate_tokens

logger = logging.getLogger(__name__)

_SUMMARY_KEYS = {
    "original_goal",
    "completed_work",
    "changed_files",
    "key_decisions",
    "failed_attempts",
    "current_constraints",
    "verification_state",
    "unresolved_work",
    "important_paths",
}


@dataclass(frozen=True)
class VerificationFact:
    kind: str
    command: str
    summary: str


@dataclass(frozen=True)
class RunCompactionFacts:
    """Deterministic state supplied by AgentLoop, never inferred from prose."""

    changed_paths: tuple[str, ...] = ()
    verification: tuple[VerificationFact, ...] = ()


@dataclass
class RunWorkingSummary:
    original_goal: str
    completed_work: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    failed_attempts: list[str] = field(default_factory=list)
    current_constraints: list[str] = field(default_factory=list)
    verification_state: str = "No code change has been recorded."
    unresolved_work: list[str] = field(default_factory=list)
    important_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PreparedContext:
    messages: list[dict[str, Any]]
    estimated_tokens: int
    compacted: bool = False


class SummaryEnhancer(Protocol):
    def enhance(
        self, summary: RunWorkingSummary, trace: list[dict[str, Any]]
    ) -> RunWorkingSummary: ...


class LLMSummaryEnhancer:
    """Optional soft-semantic enhancement with strict structured validation."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    def enhance(
        self, summary: RunWorkingSummary, trace: list[dict[str, Any]]
    ) -> RunWorkingSummary:
        schema_hint = {
            "original_goal": "string",
            "completed_work": ["string"],
            "changed_files": ["string"],
            "key_decisions": ["string"],
            "failed_attempts": ["string"],
            "current_constraints": ["string"],
            "verification_state": "string",
            "unresolved_work": ["string"],
            "important_paths": ["string"],
        }
        reply = self.client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Summarize an in-progress coding run as one JSON object. "
                        "Return exactly the requested keys and no Markdown. Do not "
                        "invent files, tests, success, or completion."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "schema": schema_hint,
                            "deterministic_state": asdict(summary),
                            "sanitized_trace": trace,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            [],
        )
        value = _strict_json_object(reply.text)
        enhanced = _validated_summary(value)

        # Hard facts always come from AgentLoop/tool events.  The optional model
        # may improve only the explanatory decision summary.
        return RunWorkingSummary(
            original_goal=summary.original_goal,
            completed_work=summary.completed_work,
            changed_files=summary.changed_files,
            key_decisions=_unique(enhanced.key_decisions)[:8],
            failed_attempts=summary.failed_attempts,
            current_constraints=summary.current_constraints,
            verification_state=summary.verification_state,
            unresolved_work=summary.unresolved_work,
            important_paths=summary.important_paths,
        )


class ContextBudgetGuard:
    """Build a bounded model view without mutating ``Conversation.turns``."""

    def __init__(
        self,
        *,
        enabled: bool,
        context_limit: int,
        threshold: float,
        keep_recent_messages: int,
        run_start_index: int,
        enhancer: SummaryEnhancer | None = None,
    ) -> None:
        self.enabled = enabled
        self.context_limit = max(1, int(context_limit))
        self.threshold = min(0.95, max(0.05, float(threshold)))
        self.keep_recent_messages = max(2, int(keep_recent_messages))
        self.run_start_index = max(0, int(run_start_index))
        self.enhancer = enhancer

        self.summary: RunWorkingSummary | None = None
        self.last_compaction_message_index = self.run_start_index + 1
        self.last_attempted_message_index = self.run_start_index + 1
        self.compaction_count = 0
        self._source_start = self.run_start_index + 1
        self._source_end = self.run_start_index + 1
        self._last_request_estimate = 0
        self._last_actual_prompt_tokens = 0

    def prepare(
        self,
        conversation: Conversation,
        tool_schemas: list[dict[str, Any]],
        facts: RunCompactionFacts,
    ) -> PreparedContext:
        original_messages = conversation.to_messages()
        if not self.enabled:
            return PreparedContext(
                original_messages,
                _estimate_request_tokens(original_messages, tool_schemas),
                False,
            )

        active_messages = self._active_messages(conversation)
        active_estimate = _estimate_request_tokens(active_messages, tool_schemas)
        effective_estimate = self._effective_estimate(active_estimate)
        trigger = int(self.context_limit * self.threshold)
        if effective_estimate < trigger:
            return PreparedContext(
                active_messages, active_estimate, self.summary is not None
            )

        turns = conversation.turns
        desired_start = max(
            self.run_start_index + 1,
            len(turns) - self.keep_recent_messages,
        )
        suffix_start = _protocol_safe_suffix_start(turns, desired_start)
        source_start = self.last_compaction_message_index
        source_end = max(source_start, suffix_start)

        # Nothing new can be compacted. Keep the already compacted view (if any)
        # and do not retry the same range on every model turn.
        if source_end <= source_start or source_end <= self.last_attempted_message_index:
            return PreparedContext(
                active_messages, active_estimate, self.summary is not None
            )

        source = turns[source_start:source_end]
        if not any(turn.message.get("role") != "user" for turn in source):
            return PreparedContext(
                active_messages, active_estimate, self.summary is not None
            )

        self.last_attempted_message_index = source_end
        deterministic = _build_summary(
            conversation,
            self.run_start_index,
            source,
            facts,
            previous=self.summary,
        )
        chosen = deterministic
        if self.enhancer is not None:
            try:
                chosen = self.enhancer.enhance(
                    deterministic, _sanitized_trace(source)
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "compaction_failed source=%s:%s error=%s",
                    source_start,
                    source_end,
                    exc,
                )
                # Below the provider's hard limit, preserve the exact original
                # view. At/above the limit, deterministic event facts are safer
                # than sending a predictably oversized request.
                if effective_estimate < self.context_limit:
                    return PreparedContext(
                        active_messages, active_estimate, self.summary is not None
                    )

        self.summary = chosen
        self.last_compaction_message_index = source_end
        self._source_start = min(self._source_start, source_start)
        self._source_end = source_end
        self.compaction_count += 1
        compacted_messages = self._active_messages(conversation)
        compacted_estimate = _estimate_request_tokens(
            compacted_messages, tool_schemas
        )
        return PreparedContext(compacted_messages, compacted_estimate, True)

    def observe(self, *, prompt_tokens: int, estimated_tokens: int) -> None:
        """Prefer real prior input usage when the provider reports it."""
        self._last_request_estimate = max(0, int(estimated_tokens))
        self._last_actual_prompt_tokens = max(0, int(prompt_tokens))

    def _effective_estimate(self, current_estimate: int) -> int:
        if not self._last_actual_prompt_tokens or not self._last_request_estimate:
            return current_estimate
        delta = current_estimate - self._last_request_estimate
        return max(0, self._last_actual_prompt_tokens + delta)

    def _active_messages(self, conversation: Conversation) -> list[dict[str, Any]]:
        if self.summary is None:
            return conversation.to_messages()

        all_messages = conversation.to_messages()
        # System is outside turns. Keep every pre-Run semantic message and the
        # current Run's original User Goal exactly as stored.
        prefix_end = self.run_start_index + 2
        prefix = all_messages[:prefix_end]
        compacted_turns = conversation.turns[
            self.run_start_index + 1 : self.last_compaction_message_index
        ]
        protected_constraints = [
            dict(turn.message)
            for turn in compacted_turns
            if turn.message.get("role") == "user"
        ]
        summary_message = {
            "role": "assistant",
            "content": (
                "[COMPACTED RUN STATE]\n"
                f"source_turn_range={self._source_start}:{self._source_end}; "
                f"compaction_count={self.compaction_count}\n"
                "This is a kernel-generated Working Memory summary, not proof "
                "of completion. Current files must be re-read when exact content "
                "is needed.\n"
                + json.dumps(asdict(self.summary), ensure_ascii=False, indent=2)
            ),
        }
        suffix = [
            dict(turn.message)
            for turn in conversation.turns[self.last_compaction_message_index :]
        ]
        return prefix + [summary_message] + protected_constraints + suffix


def build_optional_enhancer(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
    enable_thinking: bool | None,
) -> SummaryEnhancer | None:
    if not model.strip():
        return None
    return LLMSummaryEnhancer(
        LLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model.strip(),
            max_output_tokens=1400,
            enable_thinking=enable_thinking,
            timeout_seconds=timeout_seconds,
            max_attempts=1,
        )
    )


def _build_summary(
    conversation: Conversation,
    run_start_index: int,
    source: list[Turn],
    facts: RunCompactionFacts,
    *,
    previous: RunWorkingSummary | None,
) -> RunWorkingSummary:
    goal = str(conversation.turns[run_start_index].message.get("content") or "")
    completed = list(previous.completed_work) if previous else []
    decisions = list(previous.key_decisions) if previous else []
    failed = list(previous.failed_attempts) if previous else []
    constraints = list(previous.current_constraints) if previous else []
    paths = list(previous.important_paths) if previous else []

    for turn in source:
        message = turn.message
        role = message.get("role")
        if role == "user":
            content = str(message.get("content") or "").strip()
            if content:
                constraints.append(_clip_line(content, 500))
        elif role == "assistant":
            text = str(message.get("content") or "").strip()
            if text:
                decisions.append(_clip_line(text, 240))
            for call in message.get("tool_calls", []) or []:
                function = call.get("function") or {}
                name = str(function.get("name") or "tool")
                path = _path_from_arguments(function.get("arguments"))
                if path:
                    paths.append(path)
                completed.append(f"Requested {name}" + (f" for {path}" if path else ""))
        elif role == "tool":
            path = turn.tool_path.strip()
            if path:
                paths.append(path)
            if turn.tool_name == "read_file":
                if turn.stale:
                    item = (
                        f"Inspected {path or 'a file'}, but that snapshot became "
                        "stale after a modification; re-read before exact edits."
                    )
                else:
                    item = f"Inspected {path or 'a file'}."
            else:
                detail = turn.tool_summary or str(message.get("content") or "")
                item = _clip_line(detail, 240) or f"Ran {turn.tool_name or 'tool'}"
            if turn.tool_ok is False:
                failed.append(item)
            else:
                completed.append(item)

    changed = sorted(set(facts.changed_paths))
    verification = _verification_state(facts)
    unresolved: list[str] = []
    if changed and not facts.verification:
        unresolved.append(
            "The latest authored file changes still require a successful "
            "test, build, or static-check command."
        )
    return RunWorkingSummary(
        original_goal=goal,
        completed_work=_unique(completed)[-20:],
        changed_files=changed,
        key_decisions=_unique(decisions)[-8:],
        failed_attempts=_unique(failed)[-10:],
        current_constraints=_unique(constraints)[-10:],
        verification_state=verification,
        unresolved_work=unresolved,
        important_paths=_unique(paths + changed)[-24:],
    )


def _verification_state(facts: RunCompactionFacts) -> str:
    if not facts.changed_paths:
        return "No authored file change has been recorded in this Run."
    if not facts.verification:
        return "DIRTY: latest authored changes have no successful verification evidence."
    latest = facts.verification[-1]
    return (
        "VERIFIED evidence exists after the latest change: "
        f"{latest.kind} {latest.command} ({latest.summary}). "
        "AgentLoop remains the sole authority for final completion."
    )


def _sanitized_trace(turns: list[Turn]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for turn in turns:
        message = turn.message
        role = str(message.get("role") or "")
        if role == "tool":
            if turn.tool_name == "read_file":
                text = (
                    "stale read removed; re-read required"
                    if turn.stale
                    else "file inspected; raw source intentionally omitted"
                )
            else:
                text = _clip_line(
                    turn.tool_summary or str(message.get("content") or ""), 240
                )
            trace.append(
                {
                    "role": "tool",
                    "tool": turn.tool_name,
                    "path": turn.tool_path,
                    "ok": turn.tool_ok,
                    "summary": text,
                }
            )
        elif role == "assistant":
            trace.append(
                {
                    "role": "assistant",
                    "text": _clip_line(str(message.get("content") or ""), 300),
                    "tool_calls": [
                        str((call.get("function") or {}).get("name") or "")
                        for call in message.get("tool_calls", []) or []
                    ],
                }
            )
        elif role == "user":
            trace.append(
                {
                    "role": "user",
                    "text": _clip_line(str(message.get("content") or ""), 500),
                }
            )
    return trace


def _protocol_safe_suffix_start(turns: list[Turn], desired: int) -> int:
    desired = min(max(0, desired), len(turns))
    while desired > 0 and desired < len(turns):
        message = turns[desired].message
        if message.get("role") != "tool":
            break
        call_id = message.get("tool_call_id")
        desired -= 1
        assistant = turns[desired].message
        if assistant.get("role") == "assistant" and any(
            call.get("id") == call_id
            for call in assistant.get("tool_calls", []) or []
        ):
            break
    return desired


def _estimate_request_tokens(
    messages: list[dict[str, Any]], tool_schemas: list[dict[str, Any]]
) -> int:
    total = sum(
        estimate_tokens(json.dumps(message, ensure_ascii=False, default=str))
        for message in messages
    )
    total += sum(
        estimate_tokens(json.dumps(schema, ensure_ascii=False, default=str))
        for schema in tool_schemas
    )
    return total


def _strict_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        raise ValueError("compaction summary must be raw JSON without fences")
    value = json.loads(stripped)
    if not isinstance(value, dict) or set(value) != _SUMMARY_KEYS:
        raise ValueError("compaction summary has an invalid object schema")
    return value


def _validated_summary(value: dict[str, Any]) -> RunWorkingSummary:
    if not isinstance(value["original_goal"], str) or not isinstance(
        value["verification_state"], str
    ):
        raise ValueError("compaction scalar fields must be strings")
    list_keys = _SUMMARY_KEYS - {"original_goal", "verification_state"}
    for key in list_keys:
        items = value[key]
        if not isinstance(items, list) or not all(
            isinstance(item, str) for item in items
        ):
            raise ValueError(f"compaction field {key} must be a string list")
    return RunWorkingSummary(**value)


def _path_from_arguments(raw: Any) -> str:
    try:
        value = json.loads(str(raw or "{}"))
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(value, dict):
        return ""
    return str(value.get("path") or value.get("file_path") or "").strip()


def _clip_line(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
