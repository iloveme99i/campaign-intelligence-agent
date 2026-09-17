import pytest
from analytics_agent.merchant.target import evaluate_target


@pytest.mark.parametrize(
    ("campaign", "expected", "unit"),
    [
        ({"primary_metric": "conversion_rate", "target_value": "0.12"}, 0.125, "rate"),
        ({"primary_metric": "completed_orders", "target_value": "90"}, 98, "count"),
        ({"primary_metric": "net_revenue", "target_value": "500000"}, 513000, "cents"),
        ({"primary_metric": "roi", "target_value": "2.5"}, 3, "ratio"),
    ],
)
def test_evaluates_supported_campaign_targets(campaign, expected, unit):
    result = evaluate_target(
        campaign,
        [{"period": "activity", "completed_orders": 98, "net_revenue_cents": 513000}],
        [{"exposed_users": 800, "buyer_users": 100, "contribution_cents": 300000}],
        [{"activity_cost_cents": 100000}],
    )
    assert result["actual_value"] == pytest.approx(expected)
    assert result["unit"] == unit
    assert result["achieved"] is True


def test_missing_exposure_does_not_invent_zero_conversion():
    result = evaluate_target(
        {"primary_metric": "conversion_rate", "target_value": "0.12"},
        [],
        [],
        [],
    )
    assert result["status"] == "missing_denominator"
    assert result["actual_value"] is None
    assert result["achieved"] is None


def test_conversion_target_stops_when_buyer_has_no_prior_exposure():
    result = evaluate_target(
        {"primary_metric": "conversion_rate", "target_value": "0.12"},
        [],
        [],
        [],
        quality={"exposed_users": 100, "buyer_users": 20, "buyers_without_prior_exposure": 1},
    )
    assert result["status"] == "invalid_path_population"
    assert result["achieved"] is None
