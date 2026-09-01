"""Read-only Session and same-workspace history retrieval for the Web Worker.

The backend owns persistence and sends bounded Session and repository snapshots.
This module never opens SQLite or escapes the workspace scope selected upstream.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from hako.memory import RepositoryMemoryService, repository_memory_payload
from hako.tools import Tool, ToolError, ToolResult

KINDS = {"goal", "change", "verification", "approval", "failure", "summary"}


class MemoryIndex:
    def __init__(self, memories: list[dict[str, Any]] | None = None) -> None:
        self._memories: list[dict[str, Any]] = []
        self.update(memories or [])

    def update(self, memories: list[dict[str, Any]]) -> None:
        if not isinstance(memories, list) or len(memories) > 100:
            raise ToolError("memorySnapshot must be an array with at most 100 runs")
        cleaned: list[dict[str, Any]] = []
        for item in memories:
            if not isinstance(item, dict) or not isinstance(item.get("runId"), str):
                raise ToolError("memorySnapshot contains an invalid RunMemory")
            cleaned.append(deepcopy(item))
        self._memories = cleaned

    def session_context(self, limit: int = 8, budget: int = 4_000) -> str:
        lines: list[str] = []
        memory_window = self._memories[-limit:] if limit > 0 else []
        for memory in memory_window:
            changes = memory.get("changes", {})
            changed = (
                _strings(list(changes.values()))
                if isinstance(changes, dict)
                else _strings(changes)
            )
            verifications = memory.get("verifications", [])
            verified = [
                str(item.get("summary") or item.get("executedCommand") or "")
                for item in verifications
                if isinstance(item, dict) and item.get("ok") is True
            ]
            line = (
                f"- run={memory.get('runId')} status={memory.get('status')} "
                f"goal={memory.get('userGoal', '')!s}"
            )
            if changed:
                line += f" changed={', '.join(changed[:8])}"
            if verified:
                line += f" verified={'; '.join(verified[-2:])}"
            lines.append(line)
        text = "\n".join(lines)
        return _clip(text, budget)

    def search(
        self,
        query: str,
        *,
        run_id: str = "",
        kinds: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            raise ToolError("query cannot be empty")
        selected_kinds = set(kinds or KINDS)
        unknown = selected_kinds - KINDS
        if unknown:
            raise ToolError(f"unknown history kinds: {sorted(unknown)}")
        limit = max(1, min(int(limit), 8))
        terms = _terms(query)
        failure_intent = _contains_failure_intent(query)
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for ordinal, memory in enumerate(self._memories):
            if run_id and memory.get("runId") != run_id:
                continue
            searchable = _searchable(memory, selected_kinds).lower()
            score = sum(3 if term in searchable else 0 for term in terms)
            if query.lower() in searchable:
                score += 8
            if failure_intent and "failure" in selected_kinds and _has_failed_fact(memory):
                # A generic word such as "verification" appears in both successful and
                # failed runs.  Prefer deterministic failure facts when the user asks
                # specifically about an earlier error instead of letting recency win a tie.
                score += 12
            if score:
                ranked.append((score, ordinal, memory))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [_public_memory(memory) for _, _, memory in ranked[:limit]]


def make_search_session_history(
    index: MemoryIndex,
    budget: int = 6_000,
    repository_memory: RepositoryMemoryService | None = None,
) -> Tool:
    def handler(
        query: str,
        run_id: str = "",
        kinds: list[str] | None = None,
        limit: int = 8,
    ) -> ToolResult:
        exact_run_id = run_id.strip()
        session_matches = index.search(
            query, run_id=exact_run_id, kinds=kinds, limit=limit
        )
        matches = session_matches
        searched_scopes = ["session"]
        selected_scope = "session" if session_matches else "none"
        repository_search: dict[str, Any] | None = None

        # An exact Run filter intentionally remains Session-scoped. For ordinary
        # follow-ups, an empty current Session is not proof that this workspace has
        # no history, so search the bounded same-workspace snapshot automatically.
        if not session_matches and not exact_run_id and repository_memory is not None:
            searched_scopes.append("repository")
            repository_payload = repository_memory_payload(repository_memory, query)
            matches = repository_payload["matches"]
            selected_scope = "repository" if matches else "none"
            repository_search = {
                key: value
                for key, value in repository_payload.items()
                if key not in {"notice", "matches"}
            }

        payload = {
            "notice": (
                "Search order is current Session, then earlier Sessions from the same "
                "workspace when available. selectedScope identifies the returned source. "
                "Historical evidence may be stale; re-read current files before using old "
                "code details. Hard event facts override semanticSummary."
            ),
            "searchedScopes": searched_scopes,
            "selectedScope": selected_scope,
            "sessionMatchCount": len(session_matches),
            "matches": matches,
        }
        if repository_search is not None:
            payload["repositorySearch"] = repository_search
        detail = _clip(json.dumps(payload, ensure_ascii=False, indent=2), budget)
        if selected_scope == "repository":
            summary = f"history fallback: repository {len(matches)} match(es)"
        elif selected_scope == "session":
            summary = f"session history: {len(matches)} match(es)"
        elif "repository" in searched_scopes:
            summary = "history: no match in session or repository"
        else:
            summary = "session history: 0 match(es)"
        return ToolResult(
            ok=True,
            detail=detail,
            summary=summary,
        )

    return Tool(
        name="search_session_history",
        description=(
            "Search earlier goals, file changes, approvals, failures and verification evidence. "
            "The tool checks this Web Session first, then automatically falls back to earlier "
            "Sessions from the same workspace when the Session has no match. An exact run_id "
            "remains Session-scoped. Do not claim that no prior work exists unless searchedScopes "
            "includes repository and matches is empty. Re-read current files before relying on "
            "historical code details."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords describing the earlier decision, error or verification",
                },
                "run_id": {
                    "type": "string",
                    "description": "Optional exact Run id",
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(KINDS)},
                    "description": "Optional fact categories to search",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 8,
                    "description": "Maximum matches, default 8",
                },
            },
            "required": ["query"],
        },
        handler=handler,
        read_only=True,
        max_calls_per_run=5,
    )


def _searchable(memory: dict[str, Any], kinds: set[str]) -> str:
    selected: list[Any] = []
    if "goal" in kinds:
        selected.append(memory.get("userGoal"))
    if "change" in kinds:
        selected.append(memory.get("changes"))
    if "verification" in kinds:
        selected.append(memory.get("verifications"))
    if "approval" in kinds:
        selected.append(memory.get("approvals"))
    if "failure" in kinds:
        selected.extend(
            [memory.get("status"), memory.get("stopReason"), memory.get("toolFailures")]
        )
    if "summary" in kinds:
        selected.append(memory.get("semanticSummary"))
    return " ".join(_strings(selected))


def _public_memory(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(memory.get(key))
        for key in (
            "runId",
            "status",
            "stopReason",
            "userGoal",
            "changes",
            "verifications",
            "approvals",
            "toolFailures",
            "semanticSummary",
            "evidenceIds",
        )
        if key in memory
    }


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.append(str(key))
            result.extend(_strings(item))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_strings(item))
        return result
    return [str(value)]


def _terms(query: str) -> set[str]:
    terms = {part.lower() for part in re.findall(r"[A-Za-z0-9_.:/\\-]+", query)}
    for block in re.findall(r"[\u4e00-\u9fff]+", query):
        if len(block) <= 2:
            terms.add(block)
        else:
            terms.update(block[index : index + 2] for index in range(len(block) - 1))
    return {term for term in terms if term}


def _contains_failure_intent(query: str) -> bool:
    lowered = query.lower()
    return any(
        marker in lowered
        for marker in (
            "failed",
            "failure",
            "error",
            "exception",
            "\u5931\u8d25",  # 失败
            "\u9519\u8bef",  # 错误
            "\u62a5\u9519",  # 报错
            "\u5f02\u5e38",  # 异常
        )
    )


def _has_failed_fact(memory: dict[str, Any]) -> bool:
    if str(memory.get("status", "")).upper() == "FAILED":
        return True
    if memory.get("toolFailures"):
        return True
    return any(
        isinstance(item, dict)
        and (item.get("ok") is False or item.get("exitCode") not in (None, 0))
        for item in memory.get("verifications", [])
    )


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 32)] + "\n[history result truncated]"
