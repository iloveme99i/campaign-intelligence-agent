"""Deterministic campaign-target evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation


def evaluate_target(
    campaign: dict,
    outcome_rows: Sequence[dict],
    funnel_rows: Sequence[dict],
    cost_rows: Sequence[dict],
    *,
    cost_boundary: dict | None = None,
    quality: dict | None = None,
) -> dict:
    metric = str(campaign.get("primary_metric", ""))
    try:
        target = float(Decimal(str(campaign["target_value"])))
    except (KeyError, InvalidOperation, ValueError):
        return {"status": "invalid_target", "metric": metric}

    activity = next((row for row in outcome_rows if row.get("period") == "activity"), {})
    unit = "count"
    actual: float | None
    if metric == "conversion_rate":
        if quality is not None:
            exposed = int(quality.get("exposed_users") or 0)
            buyers = int(quality.get("buyer_users") or 0)
        else:
            exposed = sum(int(row.get("exposed_users", 0)) for row in funnel_rows)
            buyers = sum(int(row.get("buyer_users", 0)) for row in funnel_rows)
        if (buyers > exposed and exposed > 0) or (
            quality is not None and int(quality.get("buyers_without_prior_exposure") or 0) > 0
        ):
            return {
                "status": "invalid_path_population",
                "metric": metric,
                "target_value": target,
                "unit": "rate",
                "actual_value": None,
                "achieved": None,
                "gap": None,
            }
        actual = buyers / exposed if exposed else None
        unit = "rate"
    elif metric == "completed_orders":
        actual = float(activity.get("completed_orders", 0))
    elif metric == "net_revenue":
        actual = float(activity.get("net_revenue_cents", 0))
        unit = "cents"
    elif metric == "roi":
        if (cost_boundary or {}).get("status") == "shared_costs_unallocated":
            return {
                "status": "missing_cost_allocation",
                "metric": metric,
                "target_value": target,
                "unit": "ratio",
                "actual_value": None,
                "achieved": None,
                "gap": None,
            }
        contribution = sum(int(row.get("contribution_cents", 0)) for row in funnel_rows)
        activity_cost = sum(int(row.get("activity_cost_cents", 0)) for row in cost_rows)
        actual = contribution / activity_cost if activity_cost else None
        unit = "ratio"
    else:
        return {"status": "unsupported_metric", "metric": metric}

    return {
        "status": "evaluated" if actual is not None else "missing_denominator",
        "metric": metric,
        "actual_value": actual,
        "target_value": target,
        "unit": unit,
        "achieved": actual >= target if actual is not None else None,
        "gap": actual - target if actual is not None else None,
    }
