"""Auditable difference-in-differences for campaign incrementality panels.

The estimator deliberately accepts a balanced unit-by-period panel rather than
deriving a "control" from users who happened not to be exposed.  It reports an
effect only after composition, pre-trend and placebo checks have been evaluated.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from statistics import fmean
from typing import Any

from scipy.stats import t as student_t


class IncrementalityError(ValueError):
    """The supplied panel cannot identify the requested DiD estimand."""


def _sample_variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = fmean(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _welch_difference_in_means(
    treated: Sequence[float], control: Sequence[float], *, alpha: float
) -> dict[str, Any]:
    effect = fmean(treated) - fmean(control)
    treated_component = _sample_variance(treated) / len(treated)
    control_component = _sample_variance(control) / len(control)
    variance = treated_component + control_component
    standard_error = math.sqrt(variance)
    denominator = treated_component**2 / (len(treated) - 1) + control_component**2 / (
        len(control) - 1
    )
    degrees_of_freedom = variance**2 / denominator if denominator > 1e-24 else None
    if standard_error > 1e-12 and degrees_of_freedom is not None:
        statistic = effect / standard_error
        p_value = float(2 * student_t.sf(abs(statistic), degrees_of_freedom))
        critical = float(student_t.ppf(1 - alpha / 2, degrees_of_freedom))
        inference = "welch_t_small_sample"
    else:
        statistic = 0.0 if not effect else math.copysign(float("inf"), effect)
        p_value = 0.0 if effect else 1.0
        critical = 0.0
        inference = "degenerate_no_variance"
    return {
        "estimate": effect,
        "standard_error": standard_error,
        "statistic": statistic,
        "degrees_of_freedom": degrees_of_freedom,
        "inference": inference,
        "ci_low": effect - critical * standard_error,
        "ci_high": effect + critical * standard_error,
        "p_value": p_value,
    }


def _parallel_trend_test(
    *,
    pre_periods: Sequence[str],
    by_period: dict[str, dict[bool, list[float]]],
    alpha: float,
) -> dict[str, Any]:
    gaps = [
        fmean(by_period[period][True]) - fmean(by_period[period][False]) for period in pre_periods
    ]
    x_values = list(range(len(gaps)))
    x_mean = fmean(x_values)
    y_mean = fmean(gaps)
    sxx = sum((value - x_mean) ** 2 for value in x_values)
    slope = (
        sum(
            (x_value - x_mean) * (gap - y_mean) for x_value, gap in zip(x_values, gaps, strict=True)
        )
        / sxx
    )
    residuals = [
        gap - (y_mean + slope * (x_value - x_mean))
        for x_value, gap in zip(x_values, gaps, strict=True)
    ]
    residual_variance = sum(value**2 for value in residuals) / max(1, len(gaps) - 2)
    standard_error = math.sqrt(residual_variance / sxx) if sxx else 0.0
    if standard_error:
        degrees_of_freedom = max(1, len(gaps) - 2)
        p_value = float(2 * student_t.sf(abs(slope / standard_error), degrees_of_freedom))
    else:
        degrees_of_freedom = max(1, len(gaps) - 2)
        p_value = 1.0 if abs(slope) < 1e-12 else 0.0
    return {
        "status": "passed" if p_value >= alpha else "failed",
        "alpha": alpha,
        "slope_per_period": slope,
        "standard_error": standard_error,
        "degrees_of_freedom": degrees_of_freedom,
        "inference": "student_t_on_linear_pretrend",
        "p_value": p_value,
        "group_gaps": [
            {"period": period, "treated_minus_control": gap}
            for period, gap in zip(pre_periods, gaps, strict=True)
        ],
    }


def estimate_difference_in_differences(
    rows: Sequence[dict[str, Any]],
    *,
    metric: str,
    alpha: float = 0.05,
    validation_alpha: float = 0.10,
) -> dict[str, Any]:
    """Estimate an unweighted unit-level ATT on a balanced campaign panel.

    Required columns are ``panel_unit``, ``period``, ``treated``, ``post`` and
    ``outcome``.  Treatment assignment must be stable over time, every unit must
    be observed in the same periods, and at least three pre-periods are required.
    The result is decision-ready only when both the linear pre-trend test and a
    last-pre-period placebo test do not reject at ``validation_alpha``.
    """
    if not 0 < alpha < 0.5 or not 0 < validation_alpha < 0.5:
        raise IncrementalityError("alpha values must be between 0 and 0.5")
    if not rows:
        raise IncrementalityError("incrementality panel is empty")

    values: dict[str, dict[str, float]] = defaultdict(dict)
    assignments: dict[str, bool] = {}
    period_post: dict[str, bool] = {}
    for index, row in enumerate(rows, 1):
        try:
            unit = str(row["panel_unit"]).strip()
            period = str(row["period"]).strip()
            treated_raw = row["treated"]
            post_raw = row["post"]
            outcome = float(row["outcome"])
        except (KeyError, TypeError, ValueError) as exc:
            raise IncrementalityError(f"row {index} has an invalid panel value") from exc
        if not unit or not period or not math.isfinite(outcome):
            raise IncrementalityError(f"row {index} has an invalid panel value")
        if treated_raw not in (0, 1, False, True) or post_raw not in (0, 1, False, True):
            raise IncrementalityError(f"row {index} treated/post must be 0 or 1")
        treated, post = bool(treated_raw), bool(post_raw)
        if period in values[unit]:
            raise IncrementalityError(f"duplicate unit-period row: {unit}/{period}")
        if unit in assignments and assignments[unit] != treated:
            raise IncrementalityError(f"treatment assignment changes over time: {unit}")
        if period in period_post and period_post[period] != post:
            raise IncrementalityError(f"post flag is inconsistent within period: {period}")
        assignments[unit] = treated
        period_post[period] = post
        values[unit][period] = outcome

    periods = sorted(period_post)
    pre_periods = [period for period in periods if not period_post[period]]
    post_periods = [period for period in periods if period_post[period]]
    if len(pre_periods) < 3 or not post_periods:
        raise IncrementalityError("DiD requires at least three pre-periods and one post-period")
    if periods != [*pre_periods, *post_periods]:
        raise IncrementalityError("all pre-periods must precede all post-periods")
    expected = set(periods)
    incomplete = [unit for unit, observations in values.items() if set(observations) != expected]
    if incomplete:
        raise IncrementalityError("panel composition changes across periods")

    treated_units = sorted(unit for unit, assigned in assignments.items() if assigned)
    control_units = sorted(unit for unit, assigned in assignments.items() if not assigned)
    if len(treated_units) < 4 or len(control_units) < 4:
        raise IncrementalityError("DiD requires at least four treated and four control units")

    unit_changes = {
        unit: fmean(values[unit][period] for period in post_periods)
        - fmean(values[unit][period] for period in pre_periods)
        for unit in values
    }
    estimate = _welch_difference_in_means(
        [unit_changes[unit] for unit in treated_units],
        [unit_changes[unit] for unit in control_units],
        alpha=alpha,
    )
    by_period: dict[str, dict[bool, list[float]]] = {
        period: {
            treated: [values[unit][period] for unit in values if assignments[unit] is treated]
            for treated in (False, True)
        }
        for period in periods
    }
    parallel = _parallel_trend_test(
        pre_periods=pre_periods,
        by_period=by_period,
        alpha=validation_alpha,
    )
    placebo_changes = {
        unit: values[unit][pre_periods[-1]]
        - fmean(values[unit][period] for period in pre_periods[:-1])
        for unit in values
    }
    placebo = _welch_difference_in_means(
        [placebo_changes[unit] for unit in treated_units],
        [placebo_changes[unit] for unit in control_units],
        alpha=alpha,
    )
    placebo_status = "passed" if placebo["p_value"] >= validation_alpha else "failed"
    decision_ready = parallel["status"] == "passed" and placebo_status == "passed"
    pretrend_power = "low" if len(pre_periods) < 5 else "standard"
    pretrend_warning = (
        f"仅 {len(pre_periods)} 个活动前周期，前趋势检验的自由度与检验力有限；"
        "未拒绝检验不等于证明平行趋势成立。"
        if pretrend_power == "low"
        else None
    )
    control_pre_mean = fmean(
        values[unit][period] for unit in control_units for period in pre_periods
    )
    return {
        "status": "identified" if decision_ready else "validation_failed",
        "method": "balanced-panel difference-in-differences on unit-level pre/post changes",
        "estimand": "average_treatment_effect_on_treated_units",
        "estimand_unit": "panel_unit",
        "pre_aggregation": "mean_across_pre_periods_per_unit",
        "post_aggregation": "mean_across_post_periods_per_unit",
        "effect_interpretation": (
            "每个处理经营单元的活动后周期均值，相对其活动前周期均值的平均处理效应。"
        ),
        "metric": metric,
        "estimate": estimate["estimate"],
        "standard_error": estimate["standard_error"],
        "statistic": estimate["statistic"],
        "degrees_of_freedom": estimate["degrees_of_freedom"],
        "inference": estimate["inference"],
        "ci95": [estimate["ci_low"], estimate["ci_high"]],
        "p_value": estimate["p_value"],
        "relative_to_control_pre": (
            estimate["estimate"] / control_pre_mean if control_pre_mean else None
        ),
        "treated_units": len(treated_units),
        "control_units": len(control_units),
        "pre_periods": len(pre_periods),
        "post_periods": len(post_periods),
        "pretrend_power": pretrend_power,
        "pretrend_warning": pretrend_warning,
        "stable_composition": True,
        "parallel_trends": parallel,
        "placebo_test": {
            "status": placebo_status,
            "alpha": validation_alpha,
            "pseudo_post_period": pre_periods[-1],
            **placebo,
        },
        "decision_ready": decision_ready,
        "causal_boundary": (
            "前趋势与安慰剂检验未发现显著违背，可在同期对照、稳定构成和无干扰假设下"
            "谨慎解读 ATT；检验未拒绝不等于假设已被证明。"
            if decision_ready
            else "前趋势或安慰剂检验未通过，不得将该估计解读为活动因果增量。"
        ),
    }
