"""用户验收项的确定性提取与覆盖检查。"""

from hako.acceptance import build_acceptance_plan, evaluate_acceptance


RUN2_OPERATOR_PROMPT = """
运营反馈：同一商品范围可能同时命中多个营销活动，目前运营无法明确控制最终采用哪一个活动。

希望达到的效果：
活动列表和详情页可以查看、修改 Priority，且不能填写负数。
同一范围命中多个有效活动时，只采用 Priority 最高的活动。
同一范围出现相同 Priority 时，发布前明确提示冲突活动和原因。
"""


def item_ids(task: str) -> set[str]:
    return {item.item_id for item in build_acceptance_plan(task).items}


def test_operator_expectation_detects_frontend_without_technical_prompt() -> None:
    assert item_ids(RUN2_OPERATOR_PROMPT) == {
        "implementation",
        "frontend",
        "verification",
    }


def test_knowledge_question_has_no_delivery_plan() -> None:
    assert not build_acceptance_plan("江苏的省会是什么？").active


def test_backend_only_change_does_not_cover_explicit_page_requirement() -> None:
    plan = build_acceptance_plan(RUN2_OPERATOR_PROMPT)
    coverage = evaluate_acceptance(
        plan,
        changed_paths=("app/services/campaign_service.py", "tests/test_priority.py"),
        has_verification=True,
    )

    assert {item.item_id for item in coverage.missing} == {"frontend"}


def test_frontend_change_and_verification_cover_operator_requirement() -> None:
    plan = build_acceptance_plan(RUN2_OPERATOR_PROMPT)
    coverage = evaluate_acceptance(
        plan,
        changed_paths=(
            "app/services/campaign_service.py",
            "app/web/templates/campaign_detail.html",
        ),
        has_verification=True,
    )

    assert coverage.complete


def test_explicit_api_test_and_docs_require_their_own_delivery_surfaces() -> None:
    plan = build_acceptance_plan(
        "实现 API 接口，并补充回归测试和更新 README。验收要求：接口、测试、文档都要交付。"
    )
    assert item_ids(
        "实现 API 接口，并补充回归测试和更新 README。验收要求：接口、测试、文档都要交付。"
    ) == {
        "implementation",
        "api",
        "regression_test",
        "documentation",
        "verification",
    }
    coverage = evaluate_acceptance(
        plan,
        changed_paths=("app/api/routes.py",),
        has_verification=True,
    )
    assert {item.item_id for item in coverage.missing} == {
        "regression_test",
        "documentation",
    }
