"""Importance and version-aware consumption policies."""

from __future__ import annotations

from .models import EngineeringMemory, MemoryType


def base_importance(memory: EngineeringMemory) -> float:
    score = 0.35
    if memory.constraints:
        score += 0.20
    if memory.decisions:
        score += 0.15
    if memory.failed_operations:
        score += 0.10
    if memory.verification_commands:
        score += 0.10
    if len(memory.changed_files) >= 2:
        score += 0.05
    return min(score, 1.0)


def blend_importance(base: float, llm_importance: float | None) -> float:
    if llm_importance is None:
        return _bounded(base)
    return _bounded(0.6 * base + 0.4 * _bounded(llm_importance))


def render_for_agent(memory: EngineeringMemory) -> str:
    if memory.memory_type is MemoryType.DURABLE:
        return "[DURABLE ENGINEERING EXPERIENCE]\n" + memory.content
    if memory.memory_type is MemoryType.EVIDENCE:
        return (
            "[HISTORICAL EVIDENCE]\n"
            + memory.content
            + "\nThis describes a historical workspace state; verify the current workspace."
        )
    paths = memory.stale_paths or memory.observed_files
    path_text = ", ".join(paths[:12]) or "the referenced files"
    if memory.is_stale:
        return (
            "[STALE HISTORICAL OBSERVATION]\n"
            f"A prior run inspected {path_text}, but later edits invalidated that observation. "
            "Re-read the current workspace before reasoning about implementation details."
        )
    return (
        "[VERSION-SENSITIVE HISTORICAL OBSERVATION]\n"
        f"A prior run inspected {path_text}. Re-read the current workspace before using it "
        "as current code evidence."
    )


def _bounded(value: float) -> float:
    return max(0.0, min(float(value), 1.0))
