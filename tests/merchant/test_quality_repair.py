from unittest.mock import AsyncMock, patch

import pytest
from analytics_agent.merchant.evaluation import TraceScore
from analytics_agent.merchant.quality_repair import (
    _normalize_deterministic_contracts,
    _normalize_raw_money_units,
    build_repair_prompt,
    plan_repair,
    repair_answer_once,
)
from langchain_core.messages import AIMessage


def _score(**checks: bool) -> TraceScore:
    return TraceScore(checks=checks, evidence_ids=())


def test_plan_repairs_answer_failures_once():
    plan = plan_repair(
        _score(confirmed_scope=True, numeric_facts_cited=False, action_contract=False)
    )

    assert plan.should_attempt is True
    assert plan.blocking_checks == ()
    assert plan.failed_checks == ("numeric_facts_cited", "action_contract")


def test_repair_normalizes_raw_cent_values_to_merchant_facing_yuan():
    answer = "活动成本 206,500 分即 2,065.00 元；补充费用 900 分。"

    assert _normalize_raw_money_units(answer) == "活动成本 2,065.00 元；补充费用 9.00 元。"


def test_deterministic_contract_normalizes_shared_cost_contradiction():
    messages = [
        {"role": "user", "event_type": "TEXT", "payload": {"text": "复盘"}},
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {
                "cost_boundary": {
                    "status": "shared_costs_unallocated",
                    "attributable_cost_cents": 0,
                }
            },
        },
    ]

    assert _normalize_deterministic_contracts(
        messages,
        "经营结果无法覆盖活动投入：可归属成本为 1,666.00 元，共享费用尚未分摊。",
    ) == ("当前无法判断经营结果能否覆盖活动投入：可归属成本为 0.00 元，共享费用尚未分摊。")


def test_deterministic_contract_ignores_non_object_cost_boundary_events():
    messages = [
        {"role": "user", "event_type": "TEXT", "payload": {"text": "复盘"}},
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {"cost_boundary": "成本口径说明"},
        },
    ]

    assert _normalize_deterministic_contracts(messages, "活动成本 900 分。") == (
        "活动成本 9.00 元。"
    )


def test_deterministic_contract_prefers_confirmed_scope_cost_boundary():
    messages = [
        {"role": "user", "event_type": "TEXT", "payload": {"text": "复盘"}},
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {
                "scope_confirmed": True,
                "cost_boundary": {
                    "status": "shared_costs_unallocated",
                    "attributable_cost_cents": 166600,
                },
            },
        },
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {
                "tool_name": "diagnose_dimension",
                "cost_boundary": {
                    "status": "shared_costs_unallocated",
                    "attributable_cost_cents": 0,
                },
            },
        },
    ]

    assert (
        _normalize_deterministic_contracts(
            messages,
            "可归属成本为 0.00 元，全活动共享费用 1,666.00 元尚未分摊。",
        )
        == "可归属成本为 1,666.00 元，全活动共享费用 1,666.00 元尚未分摊。"
    )


def test_plan_does_not_hide_structural_trace_failure():
    plan = plan_repair(
        _score(confirmed_scope=False, numeric_facts_cited=False, action_contract=False)
    )

    assert plan.should_attempt is False
    assert plan.blocking_checks == ("confirmed_scope",)


def test_repair_prompt_contains_only_current_turn_evidence():
    messages = [
        {"role": "user", "event_type": "TEXT", "payload": {"text": "旧问题"}},
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {"evidence_id": "q_old00000", "rows": [{"orders": 1}]},
        },
        {"role": "assistant", "event_type": "COMPLETE", "payload": {"text": "旧答案"}},
        {
            "role": "user",
            "event_type": "TEXT",
            "payload": {
                "text": "新问题",
                "review_scope": {"campaign_id": "campaign_new"},
            },
        },
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {"evidence_id": "q_new00000", "rows": [{"orders": 9}]},
        },
        {"role": "assistant", "event_type": "COMPLETE", "payload": {"text": "新答案"}},
    ]

    prompt = build_repair_prompt(messages, ("numeric_facts_cited",))

    assert "新问题" in prompt
    assert "新答案" in prompt
    assert "q_new00000" in prompt
    assert "旧问题" not in prompt
    assert "旧答案" not in prompt
    assert "q_old00000" not in prompt


def test_repair_prompt_removes_replayed_sql_but_keeps_lineage_and_rows():
    messages = [
        {"role": "user", "event_type": "TEXT", "payload": {"text": "复盘"}},
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {
                "tool_name": "diagnose_dimension",
                "evidence_id": "q_new00000",
                "sql": "SELECT secret_repeated_payload " * 2_000,
                "parameters": {"campaign_id": "campaign-a"},
                "rows": [{"dimension_value": "A", "buyer_users": 9}],
                "evidence_manifest": [
                    {
                        "kind": "diagnosis:variant",
                        "evidence_id": "q_new00000",
                        "sql": "SELECT another_repeated_payload " * 2_000,
                    }
                ],
            },
        },
        {"role": "assistant", "event_type": "COMPLETE", "payload": {"text": "旧答案"}},
    ]

    prompt = build_repair_prompt(messages, ("numeric_facts_cited",))

    assert "q_new00000" in prompt
    assert "buyer_users" in prompt
    assert "secret_repeated_payload" not in prompt
    assert "another_repeated_payload" not in prompt
    assert len(prompt) < 8_000


def test_repair_feedback_uses_same_scope_diagnostic_money_evidence():
    import json

    messages = [
        {"role": "user", "event_type": "TEXT", "payload": {"text": "复盘"}},
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {
                "scope_confirmed": True,
                "scope_id": "current",
                "rows": [{"period": "activity", "contribution_cents": 133200}],
            },
        },
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {
                "tool_name": "diagnose_dimension",
                "scope_id": "current",
                "rows": [{"order_contribution_cents": 44800}],
            },
        },
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {
                "tool_name": "diagnose_dimension",
                "scope_id": "old",
                "rows": [{"order_contribution_cents": 99000}],
            },
        },
        {
            "role": "assistant",
            "event_type": "COMPLETE",
            "payload": {"text": "方案贡献额 448 元；另一方案贡献额 990 元。"},
        },
    ]
    packet = json.loads(build_repair_prompt(messages, ("money_values_grounded",)))
    errors = packet["detected_numeric_errors"]
    assert [error["stated_yuan"] for error in errors] == [990.0]


def test_repair_prompt_names_the_exact_ungrounded_money_claim():
    messages = [
        {
            "role": "user",
            "event_type": "TEXT",
            "payload": {"text": "复盘", "review_task": None},
        },
        {
            "role": "assistant",
            "event_type": "SQL",
            "payload": {
                "tool_name": "compare_periods",
                "scope_confirmed": True,
                "evidence_id": "q_new00000",
                "rows": [
                    {"period": "baseline", "contribution_cents": 393300},
                    {"period": "activity", "contribution_cents": 219100},
                ],
            },
        },
        {
            "role": "assistant",
            "event_type": "COMPLETE",
            "payload": {"text": "对比期贡献额 3,393.00 元，活动期贡献额 2,191.00 元。"},
        },
    ]

    prompt = build_repair_prompt(messages, ("money_values_grounded",))

    assert '"stated_yuan": 3393.0' in prompt
    assert '"label": "贡献额"' in prompt
    assert '"contribution_cents": 393300' in prompt
    assert '"baseline": 3933.0' in prompt
    assert '"activity": 2191.0' in prompt
    assert '"activity_minus_baseline_yuan": -1742.0' in prompt
    assert '"allowed_yuan"' not in prompt


@pytest.mark.asyncio
async def test_repair_accepts_an_answer_that_passes_every_gate():
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(
        content="修正版",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    original = _score(confirmed_scope=True, numeric_facts_cited=False)
    repaired = _score(confirmed_scope=True, numeric_facts_cited=True)
    with patch(
        "analytics_agent.merchant.quality_repair.score_trace",
        side_effect=[original, repaired],
    ):
        result = await repair_answer_once([], model=model)

    assert result is not None
    assert result.accepted is True
    assert result.answer == "修正版"
    assert result.usage["total_tokens"] == 15
    assert result.usage_events == ({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},)
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_repair_rejects_partial_improvement_that_still_fails_a_gate():
    model = AsyncMock()
    model.ainvoke.side_effect = [
        AIMessage(content="只修好一部分"),
        AIMessage(content="还是只修好一部分"),
    ]
    original = _score(
        confirmed_scope=True,
        numeric_facts_cited=False,
        action_contract=False,
    )
    repaired = _score(
        confirmed_scope=True,
        numeric_facts_cited=True,
        action_contract=False,
    )
    with patch(
        "analytics_agent.merchant.quality_repair.score_trace",
        side_effect=[original, repaired, repaired],
    ):
        result = await repair_answer_once([], model=model)

    assert result is not None
    assert result.repaired_score.score > result.original_score.score
    assert result.accepted is False
    assert result.attempts == 2
    assert model.ainvoke.await_count == 2
