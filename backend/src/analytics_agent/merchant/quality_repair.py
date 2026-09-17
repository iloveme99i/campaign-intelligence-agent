"""One-shot, evidence-bound repair for merchant review answers.

The repair pass is intentionally narrower than the analysis agent: it cannot
query again or invent evidence. It may only rewrite the final answer against
the already persisted evidence packet. Structural trace failures are blockers,
not prose problems, so they never trigger this pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import orjson
from langchain_core.messages import HumanMessage, SystemMessage

from analytics_agent.merchant.engine import build_analysis_brief
from analytics_agent.merchant.evaluation import (
    TraceScore,
    money_grounding_issues,
    score_trace,
)
from analytics_agent.merchant.scope import ReviewTask

STRUCTURAL_CHECKS = frozenset(
    {
        "confirmed_scope",
        "deterministic_before_answer",
        "complete_query_results",
        "single_scope",
        "task_driven_diagnosis",
        "diagnostic_reason_recorded",
        "diagnostic_selective",
        "diagnosis_before_answer",
        "diagnosis_matches_task",
        "diagnostic_scope_consistent",
    }
)

CHECK_FEEDBACK = {
    "answer_present": "必须产出完整复盘结论。",
    "evidence_cited": "只能引用证据包中真实存在的 evidence_id。",
    "core_evidence_covered": "覆盖目标、经营结果、用户路径和活动成本四类核心证据。",
    "numeric_facts_cited": "已确认事实中每条含数字的列表项都要同行标注 evidence_id。",
    "numeric_conclusion_cited": "总盘结论里的经营数字要同行标注 evidence_id。",
    "diagnosis_evidence_cited": "交代每次定向诊断结果并引用该诊断自己的 evidence_id。",
    "diagnosis_supports_conclusion": "任务判断必须由定向诊断证据直接支撑。",
    "discount_amount_sanity": "修正优惠金额的单位或数量级。",
    "money_unit_scale_sanity": "所有 *_cents 金额先除以 100，再以元呈现。",
    "money_values_grounded": "不得写证据包无法反查的金额。",
    "decision_coverage": "回答目标/结果、路径、成本和下一步决策。",
    "fact_hypothesis_separation": "严格分开已确认事实、解释假设和待验证项。",
    "action_contract": "行动必须写清对象、验证指标、护栏指标和停止/继续条件。",
    "causal_boundary": "明确说明前后变化不能证明活动因果。",
    "incrementality_estimand_clarity": (
        "将 ATT 明确写成每个处理经营单元的活动后周期均值，"
        "相对其活动前周期均值的平均处理效应；不得写成全活动总增量。"
    ),
    "incrementality_pretrend_power_clarity": (
        "当前期周期较少且 pretrend_power=low 时，明确说明"
        "前趋势检验力有限，未拒绝不等于证明平行趋势。"
    ),
    "target_cost_tension": "同时呈现目标达成与贡献下降的经营张力。",
    "cost_attribution_boundary": (
        "共享费用未分摊时，按 cost_boundary.attributable_cost_cents 展示可归属金额；"
        "全活动共享费用只能作为全活动预算参考，不能用它判断筛选门店/渠道的"
        "经营结果已覆盖或无法覆盖活动投入。需明确写为‘当前无法判断能否覆盖’。"
    ),
    "no_empty_advice": "删除持续优化、加强运营、提升体验等空话。",
    "no_internal_field_leakage": (
        "删除 decision_ready、descriptive_signal、scope_id 等内部字段名，"
        "改写成商家能直接理解的业务判断。"
    ),
    "no_raw_money_units": "删除原始‘分’值，面向商家只保留换算后的元。",
    "no_unsupported_causal_claim": "把无识别依据的因果措辞改成同期变化或待验证假设。",
    "variant_allocation_boundary": "观察性组间差异不能直接支持预算或流量倾斜。",
    "no_unqualified_forecast": "没有预测或实验依据时不得承诺未来必然变化。",
    "decision_intent_contract": (
        "直接回答用户的继续、调整、扩大或停止决策，并给出可执行的成立条件或停止条件。"
    ),
    "single_executable_primary_metric": (
        "下一轮只选择一个明确的分子/分母实验主指标；若验证最低承接节点，指标必须与该相邻路径一致。"
    ),
    "path_stage_consistency": "不要混淆激活用户、路径购买用户和全部购买用户。",
    "path_bottleneck_grounded": (
        "必须在已确认事实中显式写‘最低承接节点’，"
        "并按方案对照 path_diagnostics 的 stage_label、continuation_rate "
        "和 not_continued_users；不得混淆相邻路径指标。"
    ),
    "experiment_control_cohort": "新动作与原动作必须在同一合格人群内随机分流。",
    "rate_difference_arithmetic": (
        "统计差值前必须在同一句展示它所对应的两个购买/曝光比率，"
        "再写算术一致的百分点差；路径承接率需另句表达。"
    ),
    "statistical_metric_lineage": "明确统计检验对应的指标与分母，不得跨指标借用。",
}


@dataclass(frozen=True)
class RepairPlan:
    should_attempt: bool
    failed_checks: tuple[str, ...]
    blocking_checks: tuple[str, ...]


@dataclass(frozen=True)
class RepairResult:
    answer: str
    original_score: TraceScore
    repaired_score: TraceScore
    accepted: bool
    usage: dict[str, Any]
    usage_events: tuple[dict[str, Any], ...]
    attempts: int


def plan_repair(score: TraceScore) -> RepairPlan:
    failed = tuple(key for key, passed in score.checks.items() if not passed)
    blockers = tuple(key for key in failed if key in STRUCTURAL_CHECKS)
    repairable = tuple(key for key in failed if key in CHECK_FEEDBACK)
    return RepairPlan(bool(repairable) and not blockers, failed, blockers)


def _latest_turn(messages: list[dict]) -> list[dict]:
    last_user = max(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user" and message.get("event_type") == "TEXT"
        ),
        default=0,
    )
    return messages[last_user:]


def _answer_from(messages: list[dict]) -> str:
    return next(
        (
            str(message.get("payload", {}).get("text", "")).strip()
            for message in reversed(messages)
            if message.get("role") == "assistant"
            and message.get("event_type") == "COMPLETE"
            and str(message.get("payload", {}).get("text", "")).strip()
        ),
        "",
    )


def _compact_manifest(value: Any) -> list[dict[str, Any]]:
    """Keep evidence lineage while excluding repeated SQL and parameters."""
    return [
        {
            key: item.get(key)
            for key in (
                "kind",
                "evidence_id",
                "snapshot_id",
                "scope_id",
                "metric_version",
                "provenance",
                "row_count",
                "truncated",
                "elapsed_ms",
            )
            if item.get(key) is not None
        }
        for item in value or []
        if isinstance(item, dict)
    ]


def _compact_evidence(payload: dict, review_task: dict | None) -> dict:
    """Build the smallest packet that can still ground every quality gate."""
    if payload.get("tool_name") == "compare_periods":
        task = ReviewTask.model_validate(review_task) if review_task else None
        compact = build_analysis_brief(payload, task)
    else:
        keep = (
            "tool_name",
            "diagnostic_dimension",
            "diagnostic_reason",
            "evidence_id",
            "related_evidence_ids",
            "scope_id",
            "snapshot_id",
            "metric_version",
            "columns",
            "rows",
            "truncated",
            "warnings",
            "costs",
            "cost_boundary",
            "experiment",
        )
        compact = {key: payload.get(key) for key in keep if payload.get(key) is not None}
    compact["evidence_manifest"] = _compact_manifest(payload.get("evidence_manifest"))
    # The deterministic engine and durable trace retain replayable SQL. Sending
    # it again to a prose-only repair model adds cost but no corrective signal.
    compact.pop("sql", None)
    compact.pop("parameters", None)
    return compact


def build_repair_prompt(messages: list[dict], failed_checks: tuple[str, ...]) -> str:
    """Build a bounded prompt containing only the current turn and its evidence."""
    turn = _latest_turn(messages)
    user = next(
        (
            message.get("payload", {})
            for message in turn
            if message.get("role") == "user" and message.get("event_type") == "TEXT"
        ),
        {},
    )
    review_task = user.get("review_task")
    evidence = [
        _compact_evidence(message.get("payload", {}), review_task)
        for message in turn
        if message.get("event_type") == "SQL"
    ]
    feedback = [CHECK_FEEDBACK[key] for key in failed_checks if key in CHECK_FEEDBACK]
    original_answer = _answer_from(turn)
    comparison_payload = next(
        (
            message.get("payload", {})
            for message in reversed(turn)
            if message.get("event_type") == "SQL"
            and message.get("payload", {}).get("scope_confirmed") is True
        ),
        {},
    )
    grounding_payload = {
        **comparison_payload,
        "_diagnostic_evidence": [
            message.get("payload", {})
            for message in turn
            if message.get("event_type") == "SQL"
            and message.get("payload", {}).get("tool_name") == "diagnose_dimension"
            and message.get("payload", {}).get("scope_id") == comparison_payload.get("scope_id")
        ],
    }
    numeric_issues = (
        money_grounding_issues(original_answer, grounding_payload)
        if "money_values_grounded" in failed_checks and comparison_payload
        else []
    )
    packet = {
        "user_request": user.get("text", ""),
        "review_scope": user.get("review_scope"),
        "review_task": user.get("review_task"),
        "failed_quality_checks": list(failed_checks),
        "correction_requirements": feedback,
        "original_answer": original_answer,
        "detected_numeric_errors": numeric_issues,
        "evidence_packet": evidence,
    }
    return orjson.dumps(packet, option=orjson.OPT_INDENT_2).decode()


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    return str(content or "").strip()


RAW_MONEY_UNIT_PATTERN = re.compile(
    r"(?P<cents>\d[\d,]*(?:\.\d+)?)\s*分"
    r"(?:\s*[，,]?\s*(?:即|等于|折合)\s*(?P<yuan>\d[\d,]*(?:\.\d+)?)\s*元)?"
)


def _normalize_raw_money_units(answer: str) -> str:
    """Enforce the merchant-facing yuan contract after a model rewrite.

    Unit conversion is deterministic and evidence-preserving. The product
    should not spend another model call merely to hide an internal storage
    unit that the repair model repeated next to the correct yuan value.
    """

    def replace(match: re.Match[str]) -> str:
        explicit_yuan = match.group("yuan")
        if explicit_yuan:
            return f"{explicit_yuan} 元"
        cents = Decimal(match.group("cents").replace(",", ""))
        return f"{cents / Decimal(100):,.2f} 元"

    return RAW_MONEY_UNIT_PATTERN.sub(replace, answer)


SHARED_COST_CLAIM_PATTERN = re.compile(
    r"经营结果(?:无法|不能|未能|足以|能够|已|可以).{0,6}覆盖(?:活动)?投入"
)


def _unallocated_cost_boundary(messages: list[dict]) -> dict | None:
    turn = _latest_turn(messages)
    confirmed = [
        message
        for message in turn
        if message.get("event_type") == "SQL"
        and message.get("payload", {}).get("scope_confirmed") is True
    ]
    candidates = confirmed if confirmed else turn
    for message in reversed(candidates):
        if message.get("event_type") != "SQL":
            continue
        boundary = message.get("payload", {}).get("cost_boundary")
        if isinstance(boundary, dict) and boundary.get("status") == "shared_costs_unallocated":
            return boundary
    return None


def _normalize_deterministic_contracts(messages: list[dict], answer: str) -> str:
    """Apply evidence-bound transformations that do not require model judgment."""
    normalized = _normalize_raw_money_units(answer)
    boundary = _unallocated_cost_boundary(messages)
    if boundary:
        normalized = SHARED_COST_CLAIM_PATTERN.sub(
            "当前无法判断经营结果能否覆盖活动投入", normalized
        )
        attributable = boundary.get("attributable_cost_cents")
        if isinstance(attributable, (int, float)):
            expected = Decimal(str(attributable)) / Decimal(100)
            normalized = re.sub(
                r"((?:可归属|可归因)[^，。；\n]{0,16}(?:成本|费用)(?:为|是)?\s*)"
                r"\d[\d,]*(?:\.\d+)?\s*元",
                lambda match: f"{match.group(1)}{expected:,.2f} 元",
                normalized,
            )
    return normalized


def _replace_latest_answer(messages: list[dict], answer: str) -> list[dict]:
    replaced = [dict(message) for message in messages]
    for index in range(len(replaced) - 1, -1, -1):
        message = replaced[index]
        if message.get("role") != "assistant" or message.get("event_type") != "COMPLETE":
            continue
        message = dict(message)
        message["payload"] = {**message.get("payload", {}), "text": answer}
        replaced[index] = message
        break
    return replaced


async def repair_answer_once(messages: list[dict], *, model=None) -> RepairResult | None:
    """Run at most two bounded rewrites and publish only a fully passing answer."""
    original_score = score_trace(messages)
    original_answer = _answer_from(messages)
    normalized_answer = _normalize_deterministic_contracts(messages, original_answer)
    working_messages = (
        _replace_latest_answer(messages, normalized_answer)
        if normalized_answer != original_answer
        else list(messages)
    )
    repaired_score = (
        score_trace(working_messages) if normalized_answer != original_answer else original_score
    )
    plan = plan_repair(repaired_score)
    if repaired_score.passed and normalized_answer != original_answer:
        return RepairResult(
            answer=normalized_answer,
            original_score=original_score,
            repaired_score=repaired_score,
            accepted=True,
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            usage_events=(),
            attempts=0,
        )
    if not plan.should_attempt:
        return None

    if model is None:
        from analytics_agent.agent.llm import get_llm

        model = get_llm(streaming=False)
    system = SystemMessage(
        content=(
            "你是 Campaign Intelligence 的答案质控修复器。你不能调用工具，"
            "不能增加证据包中不存在的数字、事实、归因或 evidence_id。"
            "只重写最终业务回答，不解释修改过程，不输出 JSON 或代码块。"
            "correction_requirements 是硬约束，不是建议；输出前逐项静默检查，"
            "仍违反任一项就必须继续改写。"
            "固定使用：总盘结论、已确认事实、解释假设、待验证项、下一轮行动。"
            "总盘结论中必须直接引用定向诊断 evidence_id，说清诊断如何支撑决策。"
            "detected_numeric_errors 中的 stated_yuan 已被确定性校验判错；"
            "必须回到 evidence_packet 的原始整数分值逐项替换，并重算相关差额。"
            "evidence_values 是按周期和含义标注的修复账本，"
            "不得脱离字段语义随机挑数。"
            "共享费用未分摊时，禁止比较局部经营结果与全活动共享费用后声称"
            "能够或无法覆盖投入，只能写当前无法判断。"
            "下一轮行动只能有一个实验主指标；若干预最低承接节点，主指标必须"
            "使用该相邻路径的分子和分母，其他指标只能明确标为描述性或护栏。"
            "如果表达贡献额减成本的剩额，必须写成‘扣除已归属活动成本后"
            "剩余…元的账面贡献’，不得把差额写成成本。"
        )
    )
    answer = normalized_answer
    usage_events: list[dict[str, Any]] = []
    attempts = 0
    for _ in range(2):
        current_plan = plan_repair(repaired_score)
        if not current_plan.should_attempt:
            break
        attempts += 1
        response = await model.ainvoke(
            [
                system,
                HumanMessage(
                    content=build_repair_prompt(working_messages, current_plan.failed_checks)
                ),
            ]
        )
        usage_events.append(dict(getattr(response, "usage_metadata", None) or {}))
        answer = _normalize_deterministic_contracts(
            working_messages, _text_content(response.content)
        )
        working_messages = [
            *working_messages,
            {
                "role": "assistant",
                "event_type": "COMPLETE",
                "payload": {"text": answer, "quality_retry": True},
            },
        ]
        repaired_score = score_trace(working_messages)
        if answer and repaired_score.passed:
            break
    usage = {
        key: sum(int(event.get(key, 0) or 0) for event in usage_events)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    return RepairResult(
        answer=answer,
        original_score=original_score,
        repaired_score=repaired_score,
        accepted=bool(answer) and all(repaired_score.checks.values()),
        usage=usage,
        usage_events=tuple(usage_events),
        attempts=attempts,
    )
