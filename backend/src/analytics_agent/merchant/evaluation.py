"""Deterministic quality gates for merchant-agent traces.

The evaluator deliberately scores persisted events rather than subjective prose
alone. It can run in CI without an LLM and is reused after real DeepSeek runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

SECTION_TITLES = (
    "已确认事实",
    "解释假设",
    "待验证项",
    "下一轮行动",
)

TRACE_QUALITY_GROUPS = (
    (
        "scope",
        "口径完整",
        (
            "confirmed_scope",
            "deterministic_before_answer",
            "complete_query_results",
            "single_scope",
            "diagnostic_scope_consistent",
        ),
    ),
    (
        "evidence",
        "证据闭环",
        (
            "evidence_cited",
            "core_evidence_covered",
            "numeric_facts_cited",
            "numeric_conclusion_cited",
            "diagnosis_evidence_cited",
            "diagnosis_supports_conclusion",
            "discount_amount_sanity",
            "money_unit_scale_sanity",
            "money_values_grounded",
        ),
    ),
    (
        "diagnosis",
        "诊断克制",
        (
            "task_driven_diagnosis",
            "diagnostic_reason_recorded",
            "diagnostic_selective",
            "diagnosis_before_answer",
            "diagnosis_matches_task",
        ),
    ),
    (
        "decision",
        "决策边界",
        (
            "answer_present",
            "decision_coverage",
            "fact_hypothesis_separation",
            "action_contract",
            "causal_boundary",
            "incrementality_estimand_clarity",
            "incrementality_pretrend_power_clarity",
            "target_cost_tension",
            "cost_attribution_boundary",
            "no_empty_advice",
            "no_internal_field_leakage",
            "no_raw_money_units",
            "no_unsupported_causal_claim",
            "variant_allocation_boundary",
            "no_unqualified_forecast",
            "decision_intent_contract",
            "single_executable_primary_metric",
            "path_stage_consistency",
            "path_bottleneck_grounded",
            "experiment_control_cohort",
            "rate_difference_arithmetic",
            "statistical_metric_lineage",
        ),
    ),
)


def _section(answer: str, title: str) -> str:
    """Return one named answer section without relying on Markdown shape."""
    start = re.search(re.escape(title), answer)
    if not start:
        return ""
    tail = answer[start.end() :]
    boundaries = [
        match.start()
        for other in SECTION_TITLES
        if other != title
        for match in [re.search(re.escape(other), tail)]
        if match
    ]
    return tail[: min(boundaries)] if boundaries else tail


def _numeric_fact_lines_are_cited(answer: str) -> bool:
    """Every line containing a concrete fact number must carry an evidence ID."""
    facts = _section(answer, "已确认事实")
    numeric_lines = [
        line.strip()
        for line in facts.splitlines()
        # Facts are required to be list items. Section-heading metadata can
        # contain dates or a scope hash, but is not itself a factual claim.
        if re.match(r"^\s*(?:[-*+] |\d+[.)] )", line) and re.search(r"\d", line)
    ]
    return bool(numeric_lines) and all(re.search(r"q_[0-9a-f]{8,}", line) for line in numeric_lines)


def _numeric_conclusion_lines_are_cited(answer: str) -> bool:
    """Headline business numbers need evidence even when facts repeat them later."""
    facts_start = re.search(r"(?m)^[^\n]*已确认事实", answer)
    preamble = answer[: facts_start.start()] if facts_start else answer
    numeric_lines = [
        line.strip()
        for line in preamble.splitlines()
        if line.strip()
        and not re.match(r"^#{1,6}\s", line)
        and not re.match(r"^\*{0,2}\s*\d+[.、]\s*总盘结论\s*\*{0,2}$", line.strip())
        and re.search(r"\d", line)
    ]
    return not numeric_lines or all(re.search(r"q_[0-9a-f]{8,}", line) for line in numeric_lines)


def _discount_amount_sanity(answer: str, payload: dict) -> bool:
    """Catch impossible yuan amounts attributed to non-negative merchant discounts.

    This is a narrow upper-bound check, not a general numerical fact checker.
    Daily or per-segment discounts cannot exceed the full activity discount.
    """
    activity = next(
        (row for row in payload.get("rows", []) if row.get("period") == "activity"),
        {},
    )
    total_cents = activity.get("merchant_discount_cents")
    if not isinstance(total_cents, (int, float)) or total_cents < 0:
        return True
    total_yuan = total_cents / 100
    metric_union = re.compile(
        r"净收入|营收|贡献额|经营贡献|商家(?:承担)?优惠|优惠金额|"
        r"退款金额|退款|活动成本|总投入|投入金额"
    )
    discount_pattern = re.compile(r"商家(?:承担)?优惠|优惠金额|优惠")
    for match in discount_pattern.finditer(answer):
        values = _money_values_near_metric(answer, match, metric_union)
        if any(value > total_yuan + 0.01 for value in values):
            return False
    return True


MONEY_METRICS = {
    "net_revenue_cents": re.compile(r"净收入|营收"),
    "contribution_cents": re.compile(r"贡献额|经营贡献"),
    "merchant_discount_cents": re.compile(r"商家(?:承担)?优惠|优惠金额"),
    "refund_cents": re.compile(r"退款金额|退款"),
}
COST_PATTERN = re.compile(r"(?:活动)?成本|总投入|投入金额")
MONEY_METRIC_ALIASES = {
    "contribution_cents": {"contribution_cents", "order_contribution_cents"},
}


def _money_values_near_metric(
    answer: str,
    match: re.Match[str],
    metric_union: re.Pattern[str],
) -> list[float]:
    """Extract values belonging to one Chinese money label.

    Chinese business prose commonly puts the second label after its value, for
    example ``优惠 1,374 元与 2,065 元活动成本``.  A naive
    "read until the next label" parser assigns both amounts to discount.  Keep
    the ordinary label-before-value form, but hand a conjunction-linked trailing
    amount to the following label and also recognise that label's preposed value.
    """
    number = r"\d[\d,]*(?:\.\d+)?"
    tail = answer[match.end() :]
    next_metric = metric_union.search(tail)
    hard_boundary = re.search(r"[；。\n]", tail)
    boundaries = [
        boundary.start() for boundary in (next_metric, hard_boundary) if boundary is not None
    ]
    clause = tail[: min(boundaries)] if boundaries else tail
    if next_metric is not None and (
        hard_boundary is None or next_metric.start() < hard_boundary.start()
    ):
        clause = re.sub(
            rf"(?:与|和|及)\s*{number}\s*元\s*$",
            "",
            clause,
        )

    values = [float(value.replace(",", "")) for value in re.findall(rf"({number})\s*元", clause)]
    values.extend(
        float(value.replace(",", ""))
        for value in re.findall(rf"({number})\s*[–—~～]\s*{number}\s*元", clause)
    )

    prefix = answer[max(0, match.start() - 48) : match.start()]
    preposed = re.search(rf"({number})\s*元\s*$", prefix)
    if preposed:
        values.append(float(preposed.group(1).replace(",", "")))
    return values


def _money_unit_scale_sanity(answer: str, payload: dict) -> bool:
    """Reject yuan claims that exceed the full two-period monetary envelope.

    The comparison contract stores money as integer cents. A common model
    failure is to copy a raw value such as ``258150`` and label it yuan. This
    gate uses a generous upper bound (the sum of both periods), so it catches
    unit-scale errors without rejecting period totals, differences or daily
    values.
    """
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    upper_bounds = {
        metric: sum(
            float(row[metric])
            for row in rows
            if isinstance(row.get(metric), (int, float)) and row[metric] >= 0
        )
        / 100
        for metric in MONEY_METRICS
    }
    target = payload.get("target")
    if isinstance(target, dict) and target.get("unit") == "cents":
        target_metric = {
            "net_revenue": "net_revenue_cents",
            "contribution": "contribution_cents",
        }.get(target.get("metric"))
        if target_metric:
            target_values = [
                abs(float(target[key])) / 100
                for key in ("actual_value", "target_value", "gap")
                if isinstance(target.get(key), (int, float))
            ]
            if target_values:
                upper_bounds[target_metric] = max(
                    upper_bounds.get(target_metric, 0), *target_values
                )
    cost_cents = sum(
        float(row["activity_cost_cents"])
        for row in payload.get("costs", [])
        if isinstance(row, dict)
        and isinstance(row.get("activity_cost_cents"), (int, float))
        and row["activity_cost_cents"] >= 0
    )
    if cost_cents:
        upper_bounds["activity_cost_cents"] = cost_cents / 100

    patterns = {
        **MONEY_METRICS,
        "activity_cost_cents": re.compile(r"活动成本|总投入|投入金额"),
    }
    metric_boundary = (
        r"净收入|营收|贡献额|经营贡献|商家(?:承担)?优惠|优惠金额|"
        r"退款金额|退款|活动成本|总投入|投入金额"
    )
    metric_union = re.compile(metric_boundary)
    for metric, pattern in patterns.items():
        maximum = upper_bounds.get(metric, 0)
        if maximum <= 0:
            continue
        for match in pattern.finditer(answer):
            values = _money_values_near_metric(answer, match, metric_union)
            if any(value > maximum + 0.01 for value in values):
                return False
    return True


def _collect_metric_values(value: object, metric: str) -> list[float]:
    """Collect every numeric value for one exact metric key in a tool payload."""
    found: list[float] = []
    accepted_keys = MONEY_METRIC_ALIASES.get(metric, {metric})
    if isinstance(value, dict):
        if value.get("metric") in accepted_keys:
            for key in ("estimate", "standard_error"):
                estimate_value = value.get(key)
                if isinstance(estimate_value, (int, float)):
                    found.append(abs(float(estimate_value)) / 100)
            ci95 = value.get("ci95")
            if isinstance(ci95, list):
                found.extend(
                    abs(float(bound)) / 100 for bound in ci95 if isinstance(bound, (int, float))
                )
        for key, child in value.items():
            if key in accepted_keys and isinstance(child, (int, float)):
                found.append(float(child) / 100)
            else:
                found.extend(_collect_metric_values(child, metric))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_metric_values(child, metric))
    return found


def _grounded_money_values(payload: dict, metric: str) -> set[float]:
    values = _collect_metric_values(payload, metric)
    grounded = {round(value, 2) for value in values}
    grounded.add(0.0)
    # Period-over-period changes are legitimate facts even though the tool
    # returns the two endpoints rather than a separate delta field.
    for left in values:
        for right in values:
            grounded.add(round(abs(left - right), 2))
    if metric == "activity_cost_cents" and values:
        grounded.add(round(sum(values), 2))
        grounded.add(round(sum(set(values)), 2))
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    period_values = [
        float(row[metric]) / 100 for row in rows if isinstance(row.get(metric), (int, float))
    ]
    if period_values:
        grounded.add(round(sum(period_values), 2))
    target = payload.get("target")
    target_metric = {
        "net_revenue_cents": "net_revenue",
        "contribution_cents": "contribution",
    }.get(metric)
    if (
        target_metric
        and isinstance(target, dict)
        and target.get("metric") == target_metric
        and target.get("unit") == "cents"
    ):
        for key in ("actual_value", "target_value", "gap"):
            value = target.get(key)
            if isinstance(value, (int, float)):
                grounded.add(round(abs(float(value)) / 100, 2))
    return grounded


MONEY_METRIC_LABELS = {
    "net_revenue_cents": "净收入",
    "contribution_cents": "贡献额",
    "merchant_discount_cents": "商家优惠",
    "refund_cents": "退款",
    "activity_cost_cents": "活动成本",
}


def _money_reference_values(payload: dict, metric: str) -> dict[str, object]:
    """Return a small, semantic ledger for a repair model.

    The grounding gate deliberately accepts more derived values than a model
    should see during repair.  Passing hundreds of pairwise differences back
    to the model makes the correction ambiguous, so this ledger keeps only
    named period facts, their direct change, and metric-specific estimates.
    """
    reference: dict[str, object] = {}
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    period_values = {
        str(row["period"]): round(float(row[metric]) / 100, 2)
        for row in rows
        if row.get("period") is not None and isinstance(row.get(metric), (int, float))
    }
    if period_values:
        reference["period_values_yuan"] = period_values
        if "baseline" in period_values and "activity" in period_values:
            reference["activity_minus_baseline_yuan"] = round(
                period_values["activity"] - period_values["baseline"], 2
            )

    if metric == "activity_cost_cents":
        components = [
            {
                "variant": row.get("variant"),
                "channel": row.get("channel"),
                "value_yuan": round(float(row[metric]) / 100, 2),
            }
            for row in payload.get("costs", [])
            if isinstance(row, dict) and isinstance(row.get(metric), (int, float))
        ]
        if components:
            reference["cost_components"] = components
            reference["total_cost_yuan"] = round(
                sum(float(item["value_yuan"]) for item in components), 2
            )

    incrementality = payload.get("incrementality")
    if isinstance(incrementality, dict):
        estimate = next(
            (
                item
                for item in incrementality.get("estimates", [])
                if isinstance(item, dict) and item.get("metric") == metric
            ),
            None,
        )
        if estimate:
            reference["incrementality_yuan_per_treated_unit"] = {
                "estimate": round(float(estimate["estimate"]) / 100, 2),
                "ci95": [
                    round(float(bound) / 100, 2)
                    for bound in estimate.get("ci95", [])
                    if isinstance(bound, (int, float))
                ],
            }

    target_metric = {
        "net_revenue_cents": "net_revenue",
        "contribution_cents": "contribution",
    }.get(metric)
    target = payload.get("target")
    if (
        target_metric
        and isinstance(target, dict)
        and target.get("metric") == target_metric
        and target.get("unit") == "cents"
    ):
        reference["target_yuan"] = {
            key: round(float(target[key]) / 100, 2)
            for key in ("actual_value", "target_value", "gap")
            if isinstance(target.get(key), (int, float))
        }
    return reference


def _is_contribution_after_cost_claim(
    answer: str,
    match: re.Match[str],
    value: float,
    payload: dict,
) -> bool:
    """Recognise an exact contribution-minus-cost relationship, not a cost claim."""
    activity = next(
        (row for row in payload.get("rows", []) if row.get("period") == "activity"),
        {},
    )
    contribution_cents = activity.get("contribution_cents")
    cost_cents = sum(
        float(row["activity_cost_cents"])
        for row in payload.get("costs", [])
        if isinstance(row, dict) and isinstance(row.get("activity_cost_cents"), (int, float))
    )
    if not isinstance(contribution_cents, (int, float)) or not cost_cents:
        return False
    expected = round((float(contribution_cents) - cost_cents) / 100, 2)
    if abs(value - expected) > 0.011:
        return False
    line_start = answer.rfind("\n", 0, match.start()) + 1
    line_end = answer.find("\n", match.end())
    line = answer[line_start : line_end if line_end >= 0 else len(answer)]
    return bool(
        re.search(
            r"贡献额.{0,28}(?:高于|扣除).{0,20}(?:活动)?成本",
            line,
        )
    )


def _is_grounded_timeline_slice(
    answer: str, match: re.Match[str], value: float, payload: dict, metric: str
) -> bool:
    """Validate explicitly scoped first/last-N-day subtotals against daily bins."""
    if payload.get("timeline_bin_days", 1) != 1:
        return False
    line_start = answer.rfind("\n", 0, match.start()) + 1
    line_end = answer.find("\n", match.end())
    line = answer[line_start : line_end if line_end >= 0 else len(answer)]
    amounts = list(re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*元", line))
    for amount in amounts:
        if abs(float(amount.group(1).replace(",", "")) - value) > 0.011:
            continue
        prefix = line[: amount.start()]
        slices = list(re.finditer(r"(前|后)\s*([1-9]\d*)\s*天", prefix))
        periods = list(re.finditer(r"活动期|对比期", prefix))
        if not slices or not periods:
            continue
        side, count_text = slices[-1].groups()
        period = "activity" if periods[-1].group() == "活动期" else "baseline"
        timeline = payload.get("timeline", [])
        if isinstance(timeline, dict):
            timeline = timeline.get("rows", [])
        rows = sorted(
            (
                row
                for row in timeline
                if isinstance(row, dict)
                and row.get("period") == period
                and isinstance(row.get(metric), (int, float))
            ),
            key=lambda row: int(row.get("bin_index", 0)),
        )
        count = int(count_text)
        if count > len(rows):
            continue
        selected = rows[:count] if side == "前" else rows[-count:]
        expected = sum(float(row[metric]) for row in selected) / 100
        if abs(value - expected) <= 0.011:
            return True
    return False


def money_grounding_issues(answer: str, payload: dict) -> list[dict[str, object]]:
    """Return metric-labelled yuan claims missing from deterministic evidence."""
    patterns = {
        **MONEY_METRICS,
        "activity_cost_cents": COST_PATTERN,
    }
    metric_union = re.compile(
        r"净收入|营收|贡献额|经营贡献|商家(?:承担)?优惠|优惠金额|"
        r"退款金额|退款|(?:活动)?成本|总投入|投入金额"
    )
    issues: list[dict[str, object]] = []
    seen: set[tuple[str, float]] = set()
    for metric, pattern in patterns.items():
        grounded = _grounded_money_values(payload, metric)
        if not grounded:
            continue
        for match in pattern.finditer(answer):
            stated = _money_values_near_metric(answer, match, metric_union)
            for value in stated:
                issue_key = (metric, round(value, 2))
                if (
                    all(abs(value - fact) > 0.011 for fact in grounded)
                    and not _is_grounded_timeline_slice(answer, match, value, payload, metric)
                    and not (
                        metric == "activity_cost_cents"
                        and _is_contribution_after_cost_claim(answer, match, value, payload)
                    )
                    and issue_key not in seen
                ):
                    seen.add(issue_key)
                    issues.append(
                        {
                            "metric": metric,
                            "label": MONEY_METRIC_LABELS[metric],
                            "stated_yuan": value,
                            "evidence_values": _money_reference_values(payload, metric),
                        }
                    )
    return issues


def _money_values_are_grounded(answer: str, payload: dict) -> bool:
    """Verify each metric-labelled yuan amount exists in deterministic evidence."""
    return not money_grounding_issues(answer, payload)


def _primary_metric_stages(text: str) -> set[str]:
    """Map explicit numerator/denominator pairs to measurable journey stages."""
    return {
        stage
        for stage, pattern in {
            "purchase_per_exposure": re.compile(r"购买(?:用户)?.{0,8}曝光(?:用户)?"),
            "claim_to_activation": re.compile(
                r"(?:激活|关键行动)(?:用户)?.{0,10}"
                r"(?:权益领取|领取权益|领取)(?:用户)?"
            ),
            "activation_to_path_purchase": re.compile(
                r"(?:完整路径)?购买(?:用户)?.{0,12}"
                r"(?:激活|关键行动|行动)(?:用户)?"
            ),
        }.items()
        if pattern.search(text)
    }


def _single_executable_primary_metric(action: str) -> bool:
    """Require one supported experiment denominator, not a menu of metrics."""
    line = next((line for line in action.splitlines() if "验证指标" in line), "")
    if not line:
        return False
    primary = line.split("；", 1)[0]
    return len(_primary_metric_stages(primary)) == 1


def _conclusion_text(answer: str) -> str:
    facts_start = re.search(r"(?m)^[^\n]*已确认事实", answer)
    return answer[: facts_start.start()] if facts_start else answer


def _has_unsupported_causal_claim(answer: str) -> bool:
    """Reject causal verbs in decision-bearing sections without causal evidence."""
    facts = _section(answer, "已确认事实")
    action = _section(answer, "下一轮行动")
    conclusion = _conclusion_text(answer)
    decision_text = "\n".join((conclusion, facts, action))
    return bool(
        re.search(
            r"(?:活动|优惠|折扣|投放|缺货).{0,18}(?:未?带来|导致|造成|归因于)|"
            r"(?:未?带来|导致|造成).{0,18}(?:增量|增长|下降|损失)",
            decision_text,
        )
    )


def _leaks_internal_fields(answer: str) -> bool:
    """Keep implementation keys out of merchant-facing decisions."""
    return bool(
        re.search(
            r"\b(?:decision_ready|descriptive_signal|scope_id|snapshot_id|"
            r"metric_version|cost_boundary|risk_focus|decision_intent)\b",
            answer,
            flags=re.IGNORECASE,
        )
    )


def _leaks_raw_money_units(answer: str) -> bool:
    """Merchant-facing copy may show yuan only, never raw integer fen values."""

    return bool(re.search(r"\d[\d,]*(?:\.\d+)?\s*分(?:\s|即|等于|折合|[，；。])", answer))


def _covers_decision_intent(answer: str, task: dict | None) -> bool:
    """The answer must resolve the user's decision, not only describe metrics."""

    if not task:
        return True
    text = "\n".join((_conclusion_text(answer), _section(answer, "下一轮行动")))
    intent = task.get("decision_intent")
    intent_pattern = {
        "continue": r"继续|续投",
        "adjust": r"调整|修正|改变对象",
        "scale": r"扩大|扩量|放量|追加投入",
        "stop": r"停止|停投|暂停|止损|停掉|停止追加",
    }.get(intent)
    if not intent_pattern or not re.search(intent_pattern, text):
        return False
    return bool(re.search(r"条件|前提|若|如果|当.{0,12}时|不支持", text))


def _unqualified_variant_allocation(answer: str) -> bool:
    """Catch direct resource shifts presented as a proven variant choice.

    This is a conservative language gate, not a semantic proof of safety. A
    reversible, controlled pilot remains possible if it is explicitly framed
    as validation on the same decision line.
    """
    decision_text = "\n".join((_conclusion_text(answer), _section(answer, "下一轮行动")))
    allocation = re.compile(
        r"(?:把|将).{0,60}(?:预算|流量|曝光|资源|入口位).{0,35}"
        r"(?:向|投向|倾斜|转移|迁移|扩大|增加|优先)"
    )
    qualification = re.compile(
        r"小流量|灰度|试点|随机分流|验证性|对照实验|"
        r"先.{0,20}验证|待.{0,20}验证|验证.{0,20}后"
    )
    negated = re.compile(
        r"(?:不能|不应|不得|不支持|无法).{0,28}(?:把|将).{0,70}"
        r"(?:预算|流量|曝光|资源|入口位).{0,35}(?:倾斜|转给|转向|转移|迁移|扩大|增加)"
    )
    return any(
        allocation.search(line) and not qualification.search(line) and not negated.search(line)
        for line in decision_text.splitlines()
    )


def _requires_tension_statement(payload: dict) -> bool:
    target = payload.get("target")
    rows = payload.get("rows")
    if not isinstance(target, dict) or target.get("achieved") is not True:
        return False
    if not isinstance(rows, list):
        return False
    by_period = {
        row.get("period"): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("period"), str)
    }
    baseline = by_period.get("baseline", {}).get("contribution_cents")
    activity = by_period.get("activity", {}).get("contribution_cents")
    return (
        isinstance(baseline, (int, float))
        and isinstance(activity, (int, float))
        and activity < baseline
    )


def _cost_attribution_claim_is_safe(answer: str, payload: dict) -> bool:
    """A disclaimer cannot excuse contradictory scope-specific cost conclusions."""
    boundary = payload.get("cost_boundary", {})
    if boundary.get("status") != "shared_costs_unallocated":
        return True
    disclosed = bool(
        re.search(
            r"共享.{0,16}(?:费用|成本)|(?:费用|成本).{0,16}(?:未分摊|不能归属|无法归属)",
            answer,
        )
    )
    if not disclosed:
        return False
    if re.search(
        r"经营结果(?:无法|不能|未能|足以|能够|已|可以).{0,6}覆盖(?:活动)?投入",
        _conclusion_text(answer),
    ):
        return False
    expected = boundary.get("attributable_cost_cents")
    if isinstance(expected, (int, float)):
        for match in re.finditer(
            r"(?:可归属|可归因)[^，。；\n]{0,16}(?:成本|费用)(?:为|是)?\s*"
            r"(\d[\d,]*(?:\.\d+)?)\s*元",
            answer,
        ):
            stated = float(match.group(1).replace(",", ""))
            if abs(stated - float(expected) / 100) > 0.011:
                return False
    return True


def _allowed_diagnostics(task: dict | None, user_request: str = "") -> set[str]:
    """Return defensible drill-downs from the task and the user's request."""
    if not task:
        return set()
    focus = task.get("risk_focus")
    if focus == "variant":
        allowed = {"variant"}
    elif focus == "cost":
        allowed = {"variant", "channel", "location_id"}
    else:
        allowed = {"variant", "channel", "location_id", "audience"}
    context = f"{task.get('business_context', '')} {user_request}"
    if re.search(r"经营点|门店|店铺|缺货|库存", context):
        allowed.add("location_id")
    if re.search(r"渠道|短信|推送|投放|触达", context):
        allowed.add("channel")
    if re.search(r"人群|会员|新客|老客|用户", context):
        allowed.add("audience")
    return allowed


def _claims_no_activation_purchase_gap(answer: str) -> bool:
    """Catch categorical no-gap claims when the measured stage still loses users."""
    for clause in re.split(r"[。；\n]", answer):
        if not re.search(r"任务完成|关键行动|激活", clause) or "购买" not in clause:
            continue
        if re.search(r"不意味着|不能说明|不能证明|不能据此|并非", clause):
            continue
        if re.search(r"未观察到|未出现|不存在|没有|无", clause) and re.search(
            r"断点|断链|脱节|流失", clause
        ):
            return True
    return False


PATH_STAGE_PATTERNS = {
    "exposure_to_landing": re.compile(r"曝光.{0,8}(?:访问|进入|落地页)"),
    "landing_to_claim": re.compile(r"(?:访问|落地页|进入).{0,8}(?:领取|权益)"),
    "claim_to_activation": re.compile(r"(?:领取|权益).{0,8}(?:行动|激活|任务)"),
    "activation_to_path_purchase": re.compile(
        r"(?:关键行动|行动|激活|任务完成).{0,12}(?:完整路径)?购买"
    ),
}
BOTTLENECK_LANGUAGE = re.compile(
    r"最低承接|承接最低|主要断点|核心断点|最弱环节|瓶颈|"
    r"最大(?:损耗|流失)|(?:损耗|流失)最大"
)


def _path_bottleneck_claim_is_grounded(answer: str, path_diagnostics: dict) -> bool:
    """Require bottleneck language to agree with deterministic adjacent stages."""
    rows = [row for row in path_diagnostics.get("rows", []) if isinstance(row, dict)]
    expected = {
        str(row.get("variant") or ""): str(row.get("stage_key") or "")
        for row in rows
        if row.get("stage_key") in PATH_STAGE_PATTERNS
    }
    if not expected:
        return True
    claims = [
        sentence.strip()
        for sentence in re.split(r"[。\n]", answer)
        if BOTTLENECK_LANGUAGE.search(sentence)
        and not re.search(r"假设|可能|或许|推测|待验证", sentence)
    ]
    if not claims:
        return False
    matched_variants: set[str] = set()
    unique_stages = set(expected.values())
    common_stage = next(iter(unique_stages)) if len(unique_stages) == 1 else None
    explicit_common_stage_claim = False
    for sentence in claims:
        if common_stage:
            stated_stages = {
                key for key, pattern in PATH_STAGE_PATTERNS.items() if pattern.search(sentence)
            }
            mentioned = [variant for variant in expected if variant and variant in sentence]
            # A later sentence may repeat the per-variant rates after an earlier
            # sentence has already named the shared stage. Treat that as an
            # evidence expansion, not as a second unsupported bottleneck claim.
            if not stated_stages:
                matched_variants.update(mentioned)
                continue
            if common_stage not in stated_stages or len(stated_stages) != 1:
                return False
            explicit_common_stage_claim = True
            if "验证指标" in sentence and re.search(r"对应.{0,8}最低|最低.{0,8}对应", sentence):
                primary = sentence.split("；", 1)[0]
                if _primary_metric_stages(primary) != {common_stage}:
                    return False
            matched_variants.update(mentioned or expected)
            continue
        for clause in re.split(r"[；;]", sentence):
            stated_stages = {
                key for key, pattern in PATH_STAGE_PATTERNS.items() if pattern.search(clause)
            }
            mentioned = [variant for variant in expected if variant and variant in clause]
            if mentioned:
                for variant in mentioned:
                    if expected[variant] not in stated_stages or len(stated_stages) != 1:
                        return False
                    matched_variants.add(variant)
            elif stated_stages:
                return False
    if common_stage and not explicit_common_stage_claim:
        return False
    return matched_variants == set(expected)


def _control_stays_in_randomized_cohort(action: str, variants: list[str]) -> bool:
    """A subset-only intervention cannot use another historical variant as control."""
    for clause in re.split(r"[。；\n]", action):
        if not re.search(r"随机分流|随机分组", clause):
            continue
        for selected in variants:
            if not re.search(
                rf"(?:仅对|只对|仅在|只在).{{0,20}}{re.escape(selected)}.{{0,25}}随机(?:分流|分组)",
                clause,
            ):
                continue
            for other in variants:
                if other != selected and re.search(
                    rf"{re.escape(other)}.{{0,12}}(?:作为|保留为|设置为)?(?:实验)?对照",
                    clause,
                ):
                    return False
    return True


def _rate_differences_match_displayed_rates(answer: str) -> bool:
    """A reported difference must be auditable from a displayed rate pair.

    Executive summaries may repeat a difference that is fully expanded in a
    later evidence bullet. Treat that as one claim, but never accept a value
    that disagrees with the displayed pair.
    """
    validated_differences: list[float] = []
    unexpanded_differences: list[float] = []
    for line in answer.splitlines():
        for match in re.finditer(
            r"差值\s*([+-]?\d+(?:\.\d+)?)\s*(?:(?:个)?百分点|pp)",
            line,
            flags=re.IGNORECASE,
        ):
            stated = abs(float(match.group(1)))
            preceding_text = re.split(r"[。；;]", line[: match.start()])[-1]
            preceding_rates = re.findall(r"(\d+(?:\.\d+)?)\s*%", preceding_text)
            if len(preceding_rates) < 2:
                unexpanded_differences.append(stated)
                continue
            expected = abs(float(preceding_rates[-2]) - float(preceding_rates[-1]))
            if abs(stated - expected) > 0.16:
                return False
            validated_differences.append(stated)
    return all(
        any(abs(stated - validated) <= 0.011 for validated in validated_differences)
        for stated in unexpanded_differences
    )


def _statistical_metric_lineage_is_clear(answer: str) -> bool:
    """Current inferential statistics are only computed for purchase/exposure."""
    stage_metric = re.compile(
        r"(?:激活|关键行动|任务完成)(?:后|到|→).{0,8}(?:路径)?购买(?:率|转化)?"
    )
    inferential = re.compile(r"(?:95\s*%|置信区间|p\s*[=≈<＞>])", re.IGNORECASE)
    clauses = re.split(r"[。；;\n]", answer)
    return all(
        not (stage_metric.search(clause) and inferential.search(clause)) for clause in clauses
    )


@dataclass(frozen=True)
class TraceScore:
    checks: dict[str, bool]
    evidence_ids: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    @property
    def score(self) -> float:
        return sum(self.checks.values()) / len(self.checks) if self.checks else 0.0


def score_trace(
    messages: list[dict], *, require_answer: bool = True, latest_turn_only: bool = True
) -> TraceScore:
    """Score ordering, scope integrity, evidence use, and decision coverage.

    A conversation may contain multiple valid scope revisions. By default only
    the latest user turn is scored so an earlier scope does not contaminate the
    evidence set for the current answer.
    """
    if latest_turn_only:
        last_user = max(
            (
                index
                for index, message in enumerate(messages)
                if message.get("role") == "user" and message.get("event_type") == "TEXT"
            ),
            default=0,
        )
        messages = messages[last_user:]
    sql_events = [m for m in messages if m.get("event_type") == "SQL"]
    confirmed = [m for m in sql_events if m.get("payload", {}).get("scope_confirmed")]
    user_task = next(
        (
            m.get("payload", {}).get("review_task")
            for m in messages
            if m.get("role") == "user"
            and m.get("event_type") == "TEXT"
            and isinstance(m.get("payload", {}).get("review_task"), dict)
        ),
        None,
    )
    user_request = next(
        (
            str(m.get("payload", {}).get("text", ""))
            for m in messages
            if m.get("role") == "user" and m.get("event_type") == "TEXT"
        ),
        "",
    )
    diagnosis_calls = [
        m
        for m in messages
        if m.get("event_type") == "TOOL_CALL"
        and m.get("payload", {}).get("tool_name") == "diagnose_dimension"
    ]
    diagnosis_results = [
        m for m in sql_events if m.get("payload", {}).get("tool_name") == "diagnose_dimension"
    ]
    evidence_ids = tuple(
        evidence
        for message in sql_events
        for evidence in (
            [message.get("payload", {}).get("evidence_id")]
            + list(message.get("payload", {}).get("related_evidence_ids", []))
        )
        if isinstance(evidence, str) and evidence.startswith("q_")
    )
    complete_texts = [
        str(m.get("payload", {}).get("text", "")).strip()
        for m in messages
        if m.get("event_type") == "COMPLETE" and m.get("role") == "assistant"
    ]
    streamed_texts = [
        str(m.get("payload", {}).get("text", "")).strip()
        for m in messages
        if m.get("event_type") == "TEXT" and m.get("role") == "assistant"
    ]
    # A persisted COMPLETE event is the canonical answer. TEXT rows are token
    # chunks and joining them with newlines corrupts words, evidence IDs and
    # section boundaries. The fallback keeps compatibility with interrupted
    # or legacy traces that never wrote COMPLETE.
    answer = next((text for text in reversed(complete_texts) if text), "")
    if not answer:
        answer = "".join(text for text in streamed_texts if text)
    first_answer = next(
        (
            i
            for i, m in enumerate(messages)
            if m.get("event_type") in {"TEXT", "COMPLETE"}
            and m.get("role") == "assistant"
            and str(m.get("payload", {}).get("text", "")).strip()
        ),
        None,
    )
    first_confirmed = next(
        (
            i
            for i, m in enumerate(messages)
            if m.get("event_type") == "SQL" and m.get("payload", {}).get("scope_confirmed")
        ),
        None,
    )
    cited = set(re.findall(r"q_[0-9a-f]{8,}", answer))
    available = set(evidence_ids)
    latest_payload = confirmed[-1].get("payload", {}) if confirmed else {}
    grounding_payload = {
        **latest_payload,
        "_diagnostic_evidence": [
            message.get("payload", {})
            for message in diagnosis_results
            if message.get("payload", {}).get("scope_id") == latest_payload.get("scope_id")
        ],
    }
    incrementality = latest_payload.get("incrementality")
    causal_supported = (
        isinstance(incrementality, dict)
        and incrementality.get("status") == "identified"
        and bool(incrementality.get("estimates"))
        and all(
            isinstance(estimate, dict) and estimate.get("decision_ready") is True
            for estimate in incrementality["estimates"]
        )
    )
    # Every selected diagnostic's primary query must be cited. Supporting
    # cost/quality queries are required only when their numbers are used; an
    # uncited supporting query is not itself a missing explanation.
    diagnosis_evidence = {
        evidence
        for message in diagnosis_results
        for evidence in [message.get("payload", {}).get("evidence_id")]
        if isinstance(evidence, str) and evidence.startswith("q_")
    }
    conclusion_citations = set(re.findall(r"q_[0-9a-f]{8,}", _conclusion_text(answer)))
    manifest_ids = {
        entry.get("evidence_id")
        for entry in latest_payload.get("evidence_manifest", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("evidence_id"), str)
        and entry["evidence_id"].startswith("q_")
    }
    scope_ids = {m.get("payload", {}).get("scope_id") for m in confirmed}
    scope_ids.discard(None)
    confirmed_scope_id = confirmed[-1].get("payload", {}).get("scope_id") if confirmed else None
    action = _section(answer, "下一轮行动")
    tension_required = _requires_tension_statement(latest_payload)
    shared_costs_unallocated = (
        latest_payload.get("cost_boundary", {}).get("status") == "shared_costs_unallocated"
    )
    coverage = latest_payload.get("data_coverage") or {}
    coverage_gap = isinstance(coverage, dict) and any(
        isinstance(coverage.get(expected), int)
        and isinstance(coverage.get(observed), int)
        and coverage[observed] < coverage[expected]
        for expected, observed in (
            ("baseline_days", "baseline_days_with_orders"),
            ("activity_days", "activity_days_with_orders"),
            ("activity_days", "activity_days_with_exposure"),
        )
    )
    first_diagnosis = next(
        (
            i
            for i, m in enumerate(messages)
            if m.get("event_type") == "SQL"
            and m.get("payload", {}).get("tool_name") == "diagnose_dimension"
        ),
        None,
    )
    chosen_dimensions = [
        call.get("payload", {}).get("tool_input", {}).get("dimension") for call in diagnosis_calls
    ]
    allowed_diagnostics = _allowed_diagnostics(user_task, user_request)
    observational_variant = any(
        result.get("payload", {}).get("diagnostic_dimension") == "variant"
        and result.get("payload", {}).get("experiment", {}).get("status") == "observational_readout"
        for result in diagnosis_results
    )
    funnel_rows = [row for row in latest_payload.get("funnel", []) if isinstance(row, dict)]
    all_variant_stages_have_gap = (
        bool(funnel_rows)
        and all(
            int(row.get("activated_users") or 0) > int(row.get("path_buyer_users") or 0)
            for row in funnel_rows
            if int(row.get("activated_users") or 0) > 0
        )
        and any(int(row.get("activated_users") or 0) > 0 for row in funnel_rows)
    )
    checks = {
        "confirmed_scope": bool(confirmed)
        and all(m.get("payload", {}).get("scope_id") for m in confirmed),
        "deterministic_before_answer": first_confirmed is not None
        and (first_answer is None or first_confirmed < first_answer),
        "complete_query_results": bool(confirmed)
        and all(not m.get("payload", {}).get("truncated") for m in confirmed),
        "single_scope": len(scope_ids) == 1,
        "answer_present": bool(answer) if require_answer else True,
        "evidence_cited": bool(cited) and cited.issubset(available) if require_answer else True,
        "core_evidence_covered": manifest_ids.issubset(cited)
        if require_answer and manifest_ids
        else True,
        "numeric_facts_cited": _numeric_fact_lines_are_cited(answer) if require_answer else True,
        "numeric_conclusion_cited": _numeric_conclusion_lines_are_cited(answer)
        if require_answer
        else True,
        "discount_amount_sanity": _discount_amount_sanity(answer, latest_payload)
        if require_answer
        else True,
        "money_unit_scale_sanity": _money_unit_scale_sanity(answer, latest_payload)
        if require_answer
        else True,
        "money_values_grounded": _money_values_are_grounded(answer, grounding_payload)
        if require_answer
        else True,
        "decision_coverage": all(
            re.search(pattern, answer)
            for pattern in (r"目标|结果", r"路径|转化", r"成本|投入", r"下一轮|建议")
        )
        and (
            not coverage_gap
            or bool(re.search(r"导出|漏导|缺行|缺失|零单|覆盖", _section(answer, "待验证项")))
        )
        if require_answer
        else True,
        "fact_hypothesis_separation": all(
            re.search(pattern, answer) for pattern in (r"已确认事实", r"解释假设", r"待验证")
        )
        if require_answer
        else True,
        "action_contract": bool(action)
        and all(
            re.search(pattern, action)
            for pattern in (
                r"对象|人群|实验组|渠道|经营点",
                r"验证指标",
                r"护栏指标",
                r"条件|停止|继续",
            )
        )
        if require_answer
        else True,
        "causal_boundary": bool(
            re.search(
                (
                    r"同期对照|DiD|双重差分|ATT"
                    r"|平行趋势|稳定构成|无干扰|条件性因果"
                    if causal_supported
                    else r"不能.{0,16}(证明|归因|当作).{0,12}因果|"
                    r"不能作为.{0,12}因果|相关.{0,8}因果|因果.{0,8}(不能|不代表)"
                ),
                answer,
            )
        )
        if require_answer
        else True,
        "incrementality_estimand_clarity": (
            bool(
                re.search(
                    r"(?:每个|单个|平均).{0,10}处理.{0,5}(?:经营)?单元|"
                    r"处理.{0,5}(?:经营)?单元.{0,10}(?:平均|均值)",
                    answer,
                )
                and re.search(r"活动后.{0,12}(?:周期|均值)|后周期", answer)
            )
            if require_answer and causal_supported
            else True
        ),
        "incrementality_pretrend_power_clarity": (
            bool(
                re.search(
                    r"前趋势.{0,18}(?:检验力|自由度).{0,12}(?:有限|低)|"
                    r"(?:检验力|自由度).{0,12}(?:有限|低).{0,18}前趋势|"
                    r"未拒绝.{0,12}不等于.{0,12}(?:证明|成立)",
                    answer,
                )
            )
            if require_answer
            and causal_supported
            and any(
                isinstance(estimate, dict) and estimate.get("pretrend_power") == "low"
                for estimate in incrementality.get("estimates", [])
            )
            else True
        ),
        "target_cost_tension": (
            all(
                re.search(pattern, answer) for pattern in (r"达成", r"贡献", r"下降|减少|降低|代价")
            )
            if require_answer and tension_required
            else True
        ),
        "cost_attribution_boundary": _cost_attribution_claim_is_safe(answer, latest_payload)
        if require_answer and shared_costs_unallocated
        else True,
        "no_empty_advice": not re.search(r"持续优化|加强运营|提升体验", answer)
        if require_answer
        else True,
        "no_internal_field_leakage": not _leaks_internal_fields(answer) if require_answer else True,
        "no_raw_money_units": not _leaks_raw_money_units(answer) if require_answer else True,
        "no_unsupported_causal_claim": causal_supported or not _has_unsupported_causal_claim(answer)
        if require_answer
        else True,
        "variant_allocation_boundary": not _unqualified_variant_allocation(answer)
        if require_answer and observational_variant
        else True,
        "no_unqualified_forecast": not re.search(
            r"(?:会|必然|一定).{0,12}(?:下降|压缩|增加|增长|提升|改善|恶化)",
            "\n".join((_conclusion_text(answer), action)),
        )
        if require_answer
        else True,
        "decision_intent_contract": _covers_decision_intent(answer, user_task)
        if require_answer and user_task
        else True,
        "single_executable_primary_metric": _single_executable_primary_metric(action)
        if require_answer and latest_payload.get("metric_version") == "v0.8" and user_task
        else True,
        "task_driven_diagnosis": bool(diagnosis_calls) and bool(diagnosis_results)
        if user_task
        else True,
        "diagnostic_reason_recorded": all(
            isinstance(call.get("payload", {}).get("tool_input", {}).get("reason"), str)
            and bool(call["payload"]["tool_input"]["reason"].strip())
            for call in diagnosis_calls
        )
        if user_task
        else True,
        "diagnostic_selective": (
            1 <= len(diagnosis_calls) <= 3 and len(set(chosen_dimensions)) == len(chosen_dimensions)
        )
        if user_task
        else True,
        "diagnosis_before_answer": first_diagnosis is not None
        and (first_answer is None or first_diagnosis < first_answer)
        if user_task
        else True,
        "diagnosis_evidence_cited": diagnosis_evidence.issubset(cited)
        if user_task and require_answer and diagnosis_evidence
        else True,
        "diagnosis_supports_conclusion": bool(diagnosis_evidence.intersection(conclusion_citations))
        if user_task and require_answer and diagnosis_evidence
        else True,
        "diagnosis_matches_task": bool(chosen_dimensions)
        and all(dimension in allowed_diagnostics for dimension in chosen_dimensions)
        if user_task
        else True,
        "diagnostic_scope_consistent": bool(confirmed_scope_id)
        and all(
            result.get("payload", {}).get("scope_id") == confirmed_scope_id
            for result in diagnosis_results
        )
        if user_task
        else True,
        "path_stage_consistency": not (
            all_variant_stages_have_gap and _claims_no_activation_purchase_gap(answer)
        )
        if require_answer and latest_payload.get("metric_version") == "v0.8"
        else True,
        "path_bottleneck_grounded": _path_bottleneck_claim_is_grounded(
            answer, latest_payload.get("path_diagnostics", {})
        )
        if (
            require_answer
            and latest_payload.get("metric_version") == "v0.8"
            and isinstance(latest_payload.get("path_diagnostics"), dict)
            and latest_payload["path_diagnostics"].get("status") == "evaluated"
        )
        else True,
        "experiment_control_cohort": _control_stays_in_randomized_cohort(
            action,
            [str(row.get("variant")) for row in funnel_rows if row.get("variant")],
        )
        if require_answer and latest_payload.get("metric_version") == "v0.8"
        else True,
        "rate_difference_arithmetic": _rate_differences_match_displayed_rates(answer)
        if require_answer and latest_payload.get("metric_version") == "v0.8"
        else True,
        "statistical_metric_lineage": _statistical_metric_lineage_is_clear(answer)
        if require_answer and latest_payload.get("metric_version") == "v0.8"
        else True,
    }
    return TraceScore(checks=checks, evidence_ids=evidence_ids)


def summarize_trace_quality(messages: list[dict]) -> dict:
    """Produce a user-facing audit summary from the same gates used in CI."""
    score = score_trace(messages)
    last_user = max(
        (
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user" and message.get("event_type") == "TEXT"
        ),
        default=0,
    )
    latest = messages[last_user:]
    usages = [
        message.get("payload", {}) for message in latest if message.get("event_type") == "USAGE"
    ]
    started_at = next(
        (
            message.get("created_at")
            for message in latest
            if message.get("role") == "user" and message.get("event_type") == "TEXT"
        ),
        None,
    )
    finished_at = next(
        (
            message.get("created_at")
            for message in reversed(latest)
            if message.get("event_type") == "COMPLETE"
        ),
        None,
    )
    quality_retry = next(
        (
            message.get("payload", {})
            for message in reversed(latest)
            if message.get("event_type") == "QUALITY_RETRY"
        ),
        None,
    )
    elapsed_seconds = None
    if isinstance(started_at, datetime) and isinstance(finished_at, datetime):
        elapsed_seconds = round(max(0.0, (finished_at - started_at).total_seconds()), 3)

    groups = []
    for key, label, checks in TRACE_QUALITY_GROUPS:
        passed = sum(bool(score.checks.get(check)) for check in checks)
        groups.append({"key": key, "label": label, "passed": passed, "total": len(checks)})

    return {
        "status": "passed" if score.passed else "review_required",
        "passed": sum(score.checks.values()),
        "total": len(score.checks),
        "failed_checks": [key for key, passed in score.checks.items() if not passed],
        "groups": groups,
        "evidence_count": len(set(score.evidence_ids)),
        "model_calls": len(usages),
        "input_tokens": sum(int(usage.get("input_tokens", 0) or 0) for usage in usages),
        "output_tokens": sum(int(usage.get("output_tokens", 0) or 0) for usage in usages),
        "total_tokens": sum(int(usage.get("total_tokens", 0) or 0) for usage in usages),
        "elapsed_seconds": elapsed_seconds,
        "quality_retry": quality_retry,
    }
