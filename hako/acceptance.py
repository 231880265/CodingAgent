"""从用户任务中提取可由运行事实检查的最小验收计划。

这里刻意不判断“代码语义一定正确”，也不让模型自报完成。模块只负责发现
用户明确点名的交付面（例如页面、API、回归测试），再用实际变更路径和验证
证据检查这些交付面是否至少被覆盖。更深的业务正确性仍应交给项目测试和
独立 held-out evaluation。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable


@dataclass(frozen=True)
class AcceptanceItem:
    """一条可由 hako 硬事实检查的验收项。"""

    item_id: str
    label: str
    evidence_kind: str
    source_excerpt: str = ""


@dataclass(frozen=True)
class AcceptancePlan:
    items: tuple[AcceptanceItem, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.items)


@dataclass(frozen=True)
class AcceptanceCoverage:
    covered: tuple[AcceptanceItem, ...]
    missing: tuple[AcceptanceItem, ...]

    @property
    def complete(self) -> bool:
        return not self.missing


_EXPECTATION_MARKERS = re.compile(
    r"希望达到的效果|预期(?:效果|结果)?|验收(?:标准|要求)?|需要达到|要求如下",
    re.IGNORECASE,
)
_CHANGE_INTENT = re.compile(
    r"修复|实现|新增|增加|添加|补充|重构|删除|移除|更新|调整|优化|完善|解决|创建|替换|改成"
    r"|\b(?:fix|implement|add|update|refactor|remove|create)\b",
    re.IGNORECASE,
)
_FRONTEND_SURFACE = re.compile(
    r"前端|用户界面|列表页|详情页|页面.{0,18}(?:按钮|输入|编辑|展示|显示|提示|弹窗|表单|交互)"
    r"|(?:按钮|输入框|表单|弹窗).{0,18}(?:页面|展示|显示|修改|编辑)"
    r"|\b(?:frontend|ui|page|form|button|modal)\b",
    re.IGNORECASE,
)
_API_SURFACE = re.compile(r"接口|\bapi\b|endpoint", re.IGNORECASE)
_TEST_SURFACE = re.compile(
    r"(?:新增|增加|添加|补充|编写).{0,12}(?:测试|用例)"
    r"|(?:测试|用例).{0,12}(?:新增|增加|添加|补充|编写)"
    r"|(?:回归)?(?:测试|用例).{0,8}(?:文件|交付)"
    r"|\b(?:add|write).{0,16}\b(?:test|spec)s?\b",
    re.IGNORECASE,
)
_DOC_SURFACE = re.compile(
    r"(?:新增|增加|添加|补充|更新|编写|修改).{0,12}(?:文档|README|说明)"
    r"|(?:文档|README|说明).{0,8}(?:文件|交付)"
    r"|\b(?:update|write|add).{0,16}\b(?:docs?|readme)\b",
    re.IGNORECASE,
)


def build_acceptance_plan(task: str) -> AcceptancePlan:
    """构造保守的验收计划；不确定的要求不强行制造路径约束。"""

    text = " ".join(task.split())
    marker = _EXPECTATION_MARKERS.search(text)
    # 运营人员常只描述“现象 + 希望达到的效果”，不会使用“实现/修复”等工程词。
    # 明确的预期段本身就是改动意图；普通知识问答既没有改动词，也没有该段落。
    if not text or (not _CHANGE_INTENT.search(text) and marker is None):
        return AcceptancePlan()

    expectation = _expectation_text(text)
    items = [
        AcceptanceItem(
            item_id="implementation",
            label="产生与任务对应的代码修改",
            evidence_kind="authored_change",
            source_excerpt=_excerpt(expectation),
        )
    ]
    if _FRONTEND_SURFACE.search(expectation):
        items.append(
            AcceptanceItem(
                item_id="frontend",
                label="覆盖用户明确要求的页面或交互",
                evidence_kind="frontend_change",
                source_excerpt=_excerpt(expectation),
            )
        )
    if _API_SURFACE.search(expectation):
        items.append(
            AcceptanceItem(
                item_id="api",
                label="覆盖用户明确要求的 API 或接口",
                evidence_kind="api_change",
                source_excerpt=_excerpt(expectation),
            )
        )
    if _TEST_SURFACE.search(expectation):
        items.append(
            AcceptanceItem(
                item_id="regression_test",
                label="补充用户明确要求的测试文件",
                evidence_kind="test_change",
                source_excerpt=_excerpt(expectation),
            )
        )
    if _DOC_SURFACE.search(expectation):
        items.append(
            AcceptanceItem(
                item_id="documentation",
                label="更新用户明确要求的文档",
                evidence_kind="documentation_change",
                source_excerpt=_excerpt(expectation),
            )
        )
    items.append(
        AcceptanceItem(
            item_id="verification",
            label="在最后一次修改后留下成功验证证据",
            evidence_kind="verification",
        )
    )
    return AcceptancePlan(tuple(items))


def evaluate_acceptance(
    plan: AcceptancePlan,
    *,
    changed_paths: Iterable[str],
    has_verification: bool,
) -> AcceptanceCoverage:
    paths = tuple(_normalize_path(path) for path in changed_paths if path)
    covered: list[AcceptanceItem] = []
    missing: list[AcceptanceItem] = []
    for item in plan.items:
        satisfied = _satisfied(item.evidence_kind, paths, has_verification)
        (covered if satisfied else missing).append(item)
    return AcceptanceCoverage(tuple(covered), tuple(missing))


def acceptance_nudge(coverage: AcceptanceCoverage) -> str:
    missing = "\n".join(f"- {item.label}" for item in coverage.missing)
    return (
        "[hako 验收检查] 当前执行证据尚未覆盖以下用户验收项：\n"
        f"{missing}\n"
        "请重新对照用户原始目标，继续调查并补齐缺失的交付面；不要只用文字宣布完成。"
        "完成后还必须重新运行测试、构建或静态检查。"
    )


def _expectation_text(text: str) -> str:
    marker = _EXPECTATION_MARKERS.search(text)
    return text[marker.end() :].strip(" ：:。") if marker else text


def _excerpt(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/").lstrip("./").lower()
    return PurePosixPath(normalized).as_posix()


def _satisfied(kind: str, paths: tuple[str, ...], has_verification: bool) -> bool:
    if kind == "authored_change":
        return bool(paths)
    if kind == "verification":
        return has_verification
    if kind == "frontend_change":
        return any(_is_frontend_path(path) for path in paths)
    if kind == "api_change":
        return any(_is_api_path(path) for path in paths)
    if kind == "test_change":
        return any(_is_test_path(path) for path in paths)
    if kind == "documentation_change":
        return any(_is_documentation_path(path) for path in paths)
    return False


def _is_frontend_path(path: str) -> bool:
    suffix = PurePosixPath(path).suffix
    visual_suffixes = {".vue", ".tsx", ".jsx", ".html", ".css", ".scss", ".sass", ".less"}
    if suffix in visual_suffixes:
        return True
    return suffix in {".js", ".ts"} and any(
        marker in f"/{path}" for marker in ("/frontend/", "/static/", "/client/", "/views/")
    )


def _is_api_path(path: str) -> bool:
    wrapped = f"/{path}"
    return any(marker in wrapped for marker in ("/api/", "/controllers/", "/controller/")) or bool(
        re.search(r"(?:^|/)(?:routes?|endpoints?)\.[^.]+$", path)
    )


def _is_test_path(path: str) -> bool:
    wrapped = f"/{path}"
    name = PurePosixPath(path).name
    return "/tests/" in wrapped or name.startswith("test_") or bool(
        re.search(r"\.(?:test|spec)\.[^.]+$", name)
    )


def _is_documentation_path(path: str) -> bool:
    wrapped = f"/{path}"
    name = PurePosixPath(path).name.lower()
    return "/docs/" in wrapped or name.startswith("readme") or PurePosixPath(path).suffix in {
        ".md",
        ".rst",
        ".adoc",
    }
