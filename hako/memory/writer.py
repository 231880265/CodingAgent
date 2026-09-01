"""Deterministically derive EngineeringMemory from persisted RunMemory facts."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .models import EngineeringMemory, MemoryType
from .policy import base_importance, blend_importance


class EngineeringMemoryWriter:
    def __init__(self, repository_id: str) -> None:
        self.repository_id = repository_id

    def build(self, run_memories: list[dict[str, Any]]) -> list[EngineeringMemory]:
        memories: list[EngineeringMemory] = []
        for run in run_memories:
            session_id = str(run.get("sessionId") or "")
            run_id = str(run.get("runId") or "")
            if not session_id or not run_id:
                continue
            changed = _changed_files(run.get("changes"))
            observed = _strings(run.get("observedFiles"))
            verifications = _verification_commands(run.get("verifications"))
            failures = _failure_facts(run.get("toolFailures"))
            decisions = _strings(run.get("decisions"))
            constraints = _strings(run.get("constraints"))
            summary = _summary(run.get("semanticSummary"))
            created_at = _date(run.get("finishedAt") or run.get("startedAt"))

            self._invalidate_prior_observations(memories, changed)

            evidence = EngineeringMemory(
                memory_id=_memory_id(self.repository_id, session_id, run_id, "evidence"),
                repository_id=self.repository_id,
                session_id=session_id,
                source_run_id=run_id,
                content=_evidence_content(run, changed, verifications, failures, summary),
                summary=summary,
                memory_type=MemoryType.EVIDENCE,
                changed_files=changed,
                verification_commands=verifications,
                failed_operations=failures,
                decisions=decisions,
                constraints=constraints,
                created_at=created_at,
            )
            evidence.importance = blend_importance(
                base_importance(evidence), _optional_number(run.get("llmImportance"))
            )
            memories.append(evidence)

            if decisions or constraints:
                durable = EngineeringMemory(
                    memory_id=_memory_id(self.repository_id, session_id, run_id, "durable"),
                    repository_id=self.repository_id,
                    session_id=session_id,
                    source_run_id=run_id,
                    content=_durable_content(run, decisions, constraints, summary),
                    summary=summary,
                    memory_type=MemoryType.DURABLE,
                    changed_files=deepcopy(changed),
                    verification_commands=deepcopy(verifications),
                    failed_operations=deepcopy(failures),
                    decisions=decisions,
                    constraints=constraints,
                    created_at=created_at,
                )
                durable.importance = blend_importance(
                    base_importance(durable), _optional_number(run.get("llmImportance"))
                )
                memories.append(durable)

            if observed:
                observation = EngineeringMemory(
                    memory_id=_memory_id(self.repository_id, session_id, run_id, "observation"),
                    repository_id=self.repository_id,
                    session_id=session_id,
                    source_run_id=run_id,
                    content=f"Run goal: {str(run.get('userGoal') or '').strip()}",
                    summary=summary,
                    memory_type=MemoryType.STALEABLE,
                    observed_files=observed,
                    created_at=created_at,
                )
                observation.importance = base_importance(observation)
                memories.append(observation)
        return memories

    @staticmethod
    def _invalidate_prior_observations(
        memories: list[EngineeringMemory], changed_paths: list[str]
    ) -> None:
        changed = {_path(path) for path in changed_paths}
        if not changed:
            return
        for memory in memories:
            if memory.memory_type is not MemoryType.STALEABLE:
                continue
            stale = sorted(changed & {_path(path) for path in memory.observed_files})
            if stale:
                memory.is_stale = True
                memory.stale_paths = stale


def repository_id(workspace: str) -> str:
    normalized = workspace.replace("\\", "/").rstrip("/").lower()
    return "repo-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _memory_id(repository: str, session: str, run: str, kind: str) -> str:
    raw = f"{repository}|{session}|{run}|{kind}"
    return "mem-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _evidence_content(
    run: dict[str, Any],
    changed: list[str],
    verifications: list[str],
    failures: list[str],
    summary: str | None,
) -> str:
    lines = [f"Goal: {str(run.get('userGoal') or '').strip()}"]
    if changed:
        lines.append("Changed: " + ", ".join(changed))
    if failures:
        lines.append("Failures: " + "; ".join(failures))
    if verifications:
        lines.append("Verification: " + "; ".join(verifications))
    if summary:
        lines.append("Non-authoritative run summary: " + summary)
    return "\n".join(lines)


def _durable_content(
    run: dict[str, Any], decisions: list[str], constraints: list[str], summary: str | None
) -> str:
    lines = [f"Goal: {str(run.get('userGoal') or '').strip()}"]
    if decisions:
        lines.append("Decisions: " + "; ".join(decisions))
    if constraints:
        lines.append("Constraints: " + "; ".join(constraints))
    if summary:
        lines.append("Context: " + summary)
    return "\n".join(lines)


def _changed_files(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    derived = {_path(path) for path in _strings(value.get("derived"))}
    ordered: list[str] = []
    for key in ("created", "modified", "deleted"):
        for item in _strings(value.get(key)):
            normalized = _path(item)
            if normalized not in derived and normalized not in ordered:
                ordered.append(normalized)
    return ordered


def _verification_commands(value: Any) -> list[str]:
    result: list[str] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, dict):
            continue
        command = str(item.get("executedCommand") or item.get("requestedCommand") or "").strip()
        if not command:
            continue
        status = str(item.get("status") or ("succeeded" if item.get("ok") else "failed"))
        exit_code = item.get("exitCode")
        fact = f"{command} -> {status}"
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            fact += f" (exitCode={exit_code})"
        result.append(fact)
    return result


def _failure_facts(value: Any) -> list[str]:
    result: list[str] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if isinstance(item, dict):
            tool = str(item.get("tool") or "tool")
            summary = str(item.get("summary") or item.get("status") or "failed")
            result.append(f"{tool}: {summary}")
        elif isinstance(item, str):
            result.append(item)
    return result


def _summary(value: Any) -> str | None:
    if isinstance(value, dict):
        text = value.get("text")
    else:
        text = value
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()[:1_500]


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and item.strip()]


def _date(value: Any) -> datetime:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _optional_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
