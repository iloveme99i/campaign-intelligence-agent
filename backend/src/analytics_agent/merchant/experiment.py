"""Deterministic, assumption-aware checks for campaign experiment readouts."""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import NormalDist


def _normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def assess_incrementality_readiness(
    *,
    has_pre_assignment_eligibility: bool,
    has_assignment_log: bool,
    has_concurrent_control: bool,
    randomization_unit: str | None,
    repeated_pre_periods: int,
    panel_unit_consistent: bool,
    outcome_complete: bool,
    interference_reviewed: bool,
) -> dict:
    """State which causal designs the supplied data can actually identify.

    This is a design gate, not an effect estimator. It prevents an equal-length
    before/after window or behaviorally selected non-exposed users from being
    relabelled as a control group after the campaign has run.
    """
    randomized_requirements = {
        "pre_assignment_eligibility": has_pre_assignment_eligibility,
        "assignment_log": has_assignment_log,
        "concurrent_control": has_concurrent_control,
        "randomization_unit_declared": bool(randomization_unit),
        "outcome_complete": outcome_complete,
        "interference_reviewed": interference_reviewed,
    }
    did_requirements = {
        "concurrent_control": has_concurrent_control,
        "repeated_pre_periods": repeated_pre_periods >= 3,
        "panel_unit_consistent": panel_unit_consistent,
        "outcome_complete": outcome_complete,
        "interference_reviewed": interference_reviewed,
    }
    randomized_ready = all(randomized_requirements.values())
    did_ready = all(did_requirements.values())
    if randomized_ready:
        status = "randomized_ready"
        current_design = "randomized_concurrent_control"
        recommended_method = "randomized_intention_to_treat"
        detail = "已具备预先分流、同期对照与结果完整性，可按分配组估计 ITT。"
    elif did_ready:
        status = "did_ready"
        current_design = "concurrent_control_panel"
        recommended_method = "difference_in_differences"
        detail = "具备同期对照、稳定面板与至少三个前置时期；仍需通过前趋势和安慰剂检验。"
    else:
        status = "not_identified"
        current_design = "observational_period_comparison"
        recommended_method = None
        detail = "当前只能报告同期或前后变化，不能估计活动因果增量。"
    return {
        "status": status,
        "current_design": current_design,
        "recommended_method": recommended_method,
        "detail": detail,
        "randomized_experiment": {
            "ready": randomized_ready,
            "requirements": randomized_requirements,
            "missing": [key for key, ready in randomized_requirements.items() if not ready],
        },
        "difference_in_differences": {
            "ready": did_ready,
            "requirements": did_requirements,
            "missing": [key for key, ready in did_requirements.items() if not ready],
            "required_validation": ["parallel_trends", "placebo_test", "stable_composition"],
        },
        "prohibited_shortcuts": [
            "把活动后未曝光用户直接当作随机对照",
            "仅凭等长前后窗口声称 DiD",
            "把显著的观察性组间差异写成活动增量",
        ],
    }


def assess_binary_outcome(
    *,
    control_total: int,
    control_successes: int,
    treatment_total: int,
    treatment_successes: int,
    measurement_method: str,
    randomization_verified: bool,
    guardrail_status: str,
    required_total: int | None = None,
    mde_pp: float | None = None,
) -> dict:
    """Evaluate a returned decision outcome without overstating causality.

    A randomized result is decision-ready only when assignment was verified,
    the planned fixed sample was reached, expected cells are usable, and the
    observed group sizes do not show material sample-ratio mismatch. Other
    designs are retained as directional evidence instead of being relabelled
    as experiments after the fact.
    """
    values = (control_total, control_successes, treatment_total, treatment_successes)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        raise ValueError("outcome counts must be integers")
    if control_total <= 0 or treatment_total <= 0:
        raise ValueError("both groups require a positive eligible population")
    if not 0 <= control_successes <= control_total:
        raise ValueError("control successes must be within the control population")
    if not 0 <= treatment_successes <= treatment_total:
        raise ValueError("treatment successes must be within the treatment population")
    if required_total is not None and required_total <= 0:
        raise ValueError("required_total must be positive")
    if mde_pp is not None and not 0 < mde_pp < 100:
        raise ValueError("mde_pp must be between 0 and 100")

    control_rate = control_successes / control_total
    treatment_rate = treatment_successes / treatment_total
    difference = treatment_rate - control_rate
    unpooled_se = math.sqrt(
        control_rate * (1 - control_rate) / control_total
        + treatment_rate * (1 - treatment_rate) / treatment_total
    )
    pooled = (control_successes + treatment_successes) / (control_total + treatment_total)
    pooled_se = math.sqrt(pooled * (1 - pooled) * (1 / control_total + 1 / treatment_total))
    z_score = difference / pooled_se if pooled_se else 0.0
    p_value = 2 * (1 - _normal_cdf(abs(z_score))) if pooled_se else 1.0
    ci95 = [
        (difference - 1.96 * unpooled_se) * 100,
        (difference + 1.96 * unpooled_se) * 100,
    ]
    total = control_total + treatment_total
    sample_coverage = total / required_total if required_total else None

    expected = [
        control_total * pooled,
        control_total * (1 - pooled),
        treatment_total * pooled,
        treatment_total * (1 - pooled),
    ]
    sample_check = "ok" if min(expected) >= 5 else "small_expected_cell"

    # With a planned 50/50 allocation, (n_t - n_c) / sqrt(n) is
    # approximately standard normal. A strict 1% threshold avoids treating
    # ordinary random imbalance as an SRM incident.
    allocation_z = (treatment_total - control_total) / math.sqrt(total)
    allocation_p_value = 2 * (1 - _normal_cdf(abs(allocation_z)))
    allocation_check = "ok" if allocation_p_value >= 0.01 else "sample_ratio_mismatch"

    if guardrail_status == "failed":
        status = "guardrail_failed"
        conclusion = "主指标结果不能覆盖护栏损失，当前方案不支持扩大。"
    elif measurement_method != "randomized_experiment":
        status = "observational_only"
        conclusion = "当前为非随机对比，只能作为方向性信号，不能证明方案造成了变化。"
    elif not randomization_verified:
        status = "randomization_unverified"
        conclusion = "尚未核验随机分流，结果不能作为因果效果进入扩大决策。"
    elif allocation_check != "ok":
        status = "sample_ratio_mismatch"
        conclusion = "组间样本比例异常，先排查分流或埋点再解释效果。"
    elif sample_check != "ok":
        status = "insufficient_expected_cells"
        conclusion = "成功事件过少，不满足当前近似检验条件，需要继续收集样本。"
    elif required_total is None:
        status = "unplanned_analysis"
        conclusion = "本轮没有预先绑定样本规划，结果仅作探索性判断。"
    elif total < required_total:
        status = "planned_sample_not_reached"
        conclusion = "尚未达到预设固定样本量，不能依据中途读数作最终判断。"
    elif p_value < 0.05 and difference < 0:
        status = "negative_effect"
        conclusion = "处理组主指标显著低于对照组，当前方案不支持继续扩大。"
    elif p_value < 0.05 and difference * 100 >= float(mde_pp or 0):
        status = "decision_threshold_met"
        conclusion = "主指标通过统计门槛并达到预设最小业务提升，可结合护栏进入扩大判断。"
    elif p_value < 0.05:
        status = "statistical_but_below_mde"
        conclusion = "差异通过统计门槛但未达到预设业务提升，暂不支持扩大。"
    else:
        status = "inconclusive"
        conclusion = "当前差异未通过预设统计门槛，不能断言方案有效或无效。"

    return {
        "status": status,
        "method": "双侧两比例 z 检验；差值使用未合并标准误的 95% 置信区间",
        "control_rate": control_rate,
        "treatment_rate": treatment_rate,
        "difference_pp": difference * 100,
        "ci95_difference_pp": ci95,
        "p_value": p_value,
        "sample_check": sample_check,
        "allocation_check": allocation_check,
        "allocation_p_value": allocation_p_value,
        "required_total": required_total,
        "observed_total": total,
        "sample_coverage": sample_coverage,
        "causal_readout": (
            measurement_method == "randomized_experiment"
            and randomization_verified
            and required_total is not None
            and total >= required_total
            and sample_check == "ok"
            and allocation_check == "ok"
        ),
        "conclusion": conclusion,
    }


def assess_variants(
    rows: Sequence[dict],
    *,
    cross_variant_exposed_users: int = 0,
    buyers_without_prior_exposure: int = 0,
    buyers_without_matching_variant_exposure: int = 0,
) -> dict:
    """Compare conversion rates without claiming that assignment was randomized."""
    if cross_variant_exposed_users > 0:
        return {
            "status": "contaminated_groups",
            "assumption": (
                f"发现 {cross_variant_exposed_users} 名用户跨实验组曝光；组间样本不独立，"
                "暂停显著性检验并先核查分流与身份拼接。"
            ),
            "comparisons": [],
        }
    if buyers_without_prior_exposure > 0:
        return {
            "status": "unlinked_buyers",
            "assumption": (
                f"发现 {buyers_without_prior_exposure} 名购买用户无法链接到先前曝光；"
                "购买/曝光分子分母不属于同一可验证路径，暂停组间检验。"
            ),
            "comparisons": [],
        }
    if buyers_without_matching_variant_exposure > 0:
        return {
            "status": "dimension_mismatch",
            "assumption": (
                f"发现 {buyers_without_matching_variant_exposure} 名购买用户虽有先前曝光，"
                "但订单实验组与先前曝光组不一致；各组购买/曝光分子分母不可配对，"
                "暂停组间检验并核查实验分配或订单标签。"
            ),
            "comparisons": [],
        }
    if any(int(row.get("buyer_users", 0)) > int(row.get("exposed_users", 0)) for row in rows):
        return {
            "status": "invalid_group_population",
            "assumption": "至少一个实验组的购买人数超过曝光人数，暂停组间检验并核对数据口径。",
            "comparisons": [],
        }
    usable = [
        row
        for row in rows
        if int(row.get("exposed_users", 0)) > 0 and row.get("variant") not in (None, "")
    ]
    if len(usable) < 2:
        return {
            "status": "insufficient_groups",
            "assumption": "需要至少两个有曝光用户的实验组。",
            "comparisons": [],
        }

    control = next(
        (row for row in usable if str(row["variant"]).lower() in {"control", "对照组"}),
        usable[0],
    )
    comparisons: list[dict] = []
    n_control = int(control["exposed_users"])
    x_control = int(control.get("buyer_users", 0))
    p_control = x_control / n_control

    for treatment in usable:
        if treatment is control:
            continue
        n_treatment = int(treatment["exposed_users"])
        x_treatment = int(treatment.get("buyer_users", 0))
        p_treatment = x_treatment / n_treatment
        difference = p_treatment - p_control
        unpooled_se = math.sqrt(
            p_control * (1 - p_control) / n_control + p_treatment * (1 - p_treatment) / n_treatment
        )
        pooled = (x_control + x_treatment) / (n_control + n_treatment)
        pooled_se = math.sqrt(pooled * (1 - pooled) * (1 / n_control + 1 / n_treatment))
        z_score = difference / pooled_se if pooled_se else 0.0
        p_value = 2 * (1 - _normal_cdf(abs(z_score))) if pooled_se else 1.0
        low = difference - 1.96 * unpooled_se
        high = difference + 1.96 * unpooled_se
        expected = [
            n_control * pooled,
            n_control * (1 - pooled),
            n_treatment * pooled,
            n_treatment * (1 - pooled),
        ]
        sample_check = "ok" if min(expected) >= 5 else "small_expected_cell"
        comparisons.append(
            {
                "control": str(control["variant"]),
                "treatment": str(treatment["variant"]),
                "control_rate": p_control,
                "treatment_rate": p_treatment,
                "difference_pp": difference * 100,
                "ci95_difference_pp": [low * 100, high * 100],
                "p_value": p_value,
                "descriptive_signal": p_value < 0.05 and sample_check == "ok",
                "sample_check": sample_check,
            }
        )

    return {
        "status": "observational_readout",
        "assumption": "数据文件无法验证随机分流；显著差异仍不能单独证明活动因果效果。",
        "method": "双侧两比例 z 检验，差值使用未合并标准误的 95% 置信区间",
        "comparisons": comparisons,
    }


def plan_binary_experiment(
    rows: Sequence[dict],
    *,
    activity_days: int,
    mde_pp: float,
    traffic_share: float = 1.0,
    alpha: float = 0.05,
    power: float = 0.8,
) -> dict:
    """Size a fixed-horizon, equal-allocation conversion experiment.

    The current pooled conversion rate is used only as the planning baseline.
    This function deliberately does not infer randomization validity or a
    business guardrail threshold from observational exports.
    """
    if activity_days < 1:
        raise ValueError("activity_days must be positive")
    if not 0 < mde_pp < 100:
        raise ValueError("mde_pp must be between 0 and 100")
    if not 0 < traffic_share <= 1:
        raise ValueError("traffic_share must be between 0 and 1")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be between 0 and 0.5")
    if not 0.5 < power < 1:
        raise ValueError("power must be between 0.5 and 1")

    exposed = sum(max(0, int(row.get("exposed_users", 0) or 0)) for row in rows)
    buyers = sum(max(0, int(row.get("buyer_users", 0) or 0)) for row in rows)
    if exposed == 0 or buyers > exposed:
        raise ValueError("usable exposed and buyer counts are required")

    baseline_rate = buyers / exposed
    delta = mde_pp / 100
    target_rate = baseline_rate + delta
    if target_rate >= 1:
        raise ValueError("mde_pp makes the target conversion rate invalid")

    midpoint = (baseline_rate + target_rate) / 2
    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    z_power = NormalDist().inv_cdf(power)
    numerator = (
        z_alpha * math.sqrt(2 * midpoint * (1 - midpoint))
        + z_power * math.sqrt(baseline_rate * (1 - baseline_rate) + target_rate * (1 - target_rate))
    ) ** 2
    required_per_group = math.ceil(numerator / (delta**2))
    required_total = required_per_group * 2
    daily_eligible_users = exposed / activity_days
    estimated_days = math.ceil(required_total / (daily_eligible_users * traffic_share))
    eligible_per_observed_window = exposed * traffic_share
    required_windows = math.ceil(required_total / eligible_per_observed_window)
    exceeds_observed_window = required_total > eligible_per_observed_window

    return {
        "status": "draft_requires_confirmation",
        "method": "固定样本双侧两比例检验，等比例随机分组",
        "baseline_rate": baseline_rate,
        "target_rate": target_rate,
        "mde_pp": mde_pp,
        "alpha": alpha,
        "power": power,
        "traffic_share": traffic_share,
        "required_per_group": required_per_group,
        "required_total": required_total,
        "observed_daily_eligible_users": daily_eligible_users,
        "estimated_days": estimated_days,
        "feasibility": {
            "status": (
                "exceeds_observed_window" if exceeds_observed_window else "within_observed_window"
            ),
            "reference_window_days": activity_days,
            "eligible_users_per_window": eligible_per_observed_window,
            "required_windows": required_windows,
            "detail": (
                f"按当前流量与 {activity_days} 天的同等活动周期估算，"
                f"约需 {required_windows} 个周期才能达到样本量。"
                "此方案不能在同等时长内完成；上线前需由业务确认更长周期、"
                "更多合格流量或更大的最小可检测提升。"
                if exceeds_observed_window
                else f"按当前流量估算，可在同等 {activity_days} 天活动周期内达到规划样本量。"
            ),
        },
        "planning_basis": (
            f"按当前 {activity_days} 天内 {exposed} 名符合所选指标分母的用户的日均流量估算；"
            "流量结构变化时需重新测算。"
        ),
        "readiness": [
            {
                "key": "mde",
                "label": "最小可检测提升",
                "status": "user_confirmed_input",
                "detail": "MDE 是业务与统计共同确认的规划输入，不由模型推断。",
            },
            {
                "key": "randomization",
                "label": "随机分流与样本比例",
                "status": "requires_validation",
                "detail": "上线前需确认随机化单元，并持续检查样本比例失配（SRM）。",
            },
            {
                "key": "denominator",
                "label": "分母资格早于干预",
                "status": "requires_validation",
                "detail": "须在干预前定义合格人群；若方案会改变进入所选分母的概率，当前样本估算与主指标不可直接用于该实验。",
            },
            {
                "key": "guardrail",
                "label": "贡献额护栏",
                "status": "requires_business_threshold",
                "detail": "需由业务负责人确认可接受的贡献额下降阈值。",
            },
            {
                "key": "contamination",
                "label": "同期干预与串组",
                "status": "requires_validation",
                "detail": "需排除同期活动、跨组曝光和重复触达造成的污染。",
            },
        ],
        "decision_rule": (
            "固定样本达到后再读取主指标；仅当差异通过预设显著性门槛、"
            "贡献额护栏未被触发且分流质量检查通过时，才支持扩大。"
        ),
    }
