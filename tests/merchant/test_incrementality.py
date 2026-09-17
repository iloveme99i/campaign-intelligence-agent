import pytest
from analytics_agent.merchant.incrementality import (
    IncrementalityError,
    estimate_difference_in_differences,
)


def _panel(*, drifting: bool = False) -> list[dict]:
    rows = []
    periods = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
    for treated, prefix, offsets in (
        (False, "c", [0.0, 0.4, -0.3, 0.2]),
        (True, "t", [0.1, -0.2, 0.3, -0.1]),
    ):
        for unit_index, offset in enumerate(offsets):
            for period_index, period in enumerate(periods):
                baseline = 10 + period_index + offset
                if treated and drifting and period_index < 3:
                    baseline += period_index * 2
                if period_index >= 3:
                    baseline += (3 if treated else 1) + (unit_index - 1.5) * (
                        0.2 if treated else -0.1
                    )
                rows.append(
                    {
                        "panel_unit": f"{prefix}{unit_index}",
                        "period": period,
                        "treated": int(treated),
                        "post": int(period_index >= 3),
                        "outcome": baseline,
                    }
                )
    return rows


def test_balanced_panel_returns_auditable_did_effect():
    result = estimate_difference_in_differences(_panel(), metric="completed_orders")

    assert result["status"] == "identified"
    assert result["decision_ready"] is True
    assert result["pretrend_power"] == "low"
    assert "不等于证明" in result["pretrend_warning"]
    assert result["estimate"] == pytest.approx(2.0)
    assert result["inference"] == "welch_t_small_sample"
    assert 3 <= result["degrees_of_freedom"] <= 6
    assert result["estimand_unit"] == "panel_unit"
    assert result["pre_aggregation"] == "mean_across_pre_periods_per_unit"
    assert result["post_aggregation"] == "mean_across_post_periods_per_unit"
    assert "每个处理经营单元" in result["effect_interpretation"]
    assert result["treated_units"] == result["control_units"] == 4
    assert result["pre_periods"] == 3
    assert result["parallel_trends"]["status"] == "passed"
    assert result["placebo_test"]["status"] == "passed"


def test_non_parallel_pretrend_blocks_causal_interpretation():
    result = estimate_difference_in_differences(_panel(drifting=True), metric="completed_orders")

    assert result["status"] == "validation_failed"
    assert result["decision_ready"] is False
    assert result["parallel_trends"]["status"] == "failed"
    assert "不得" in result["causal_boundary"]


def test_unbalanced_or_post_selected_panel_is_rejected():
    incomplete = _panel()
    incomplete.pop()
    with pytest.raises(IncrementalityError, match="composition changes"):
        estimate_difference_in_differences(incomplete, metric="completed_orders")

    changing = _panel()
    changing[-1]["treated"] = 0
    with pytest.raises(IncrementalityError, match="assignment changes"):
        estimate_difference_in_differences(changing, metric="completed_orders")


def test_too_few_operating_units_is_not_presented_as_decision_ready():
    undersized = [row for row in _panel() if row["panel_unit"] not in {"c3", "t3"}]

    with pytest.raises(IncrementalityError, match="at least four treated and four control"):
        estimate_difference_in_differences(undersized, metric="completed_orders")
