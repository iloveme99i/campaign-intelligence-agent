"""User-confirmed campaign scope and deterministic query construction."""

import hashlib
from datetime import date
from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field, model_validator


def latest_confirmed_scope(messages: list) -> "ReviewScope | None":
    for message in reversed(messages):
        if message.role != "user" or message.event_type != "TEXT":
            continue
        payload = (
            orjson.loads(message.payload) if isinstance(message.payload, str) else message.payload
        )
        if payload.get("review_scope") is not None:
            return ReviewScope.model_validate(payload["review_scope"])
    return None


def latest_review_task(messages: list) -> "ReviewTask | None":
    for message in reversed(messages):
        if message.role != "user" or message.event_type != "TEXT":
            continue
        payload = (
            orjson.loads(message.payload) if isinstance(message.payload, str) else message.payload
        )
        if payload.get("review_task") is not None:
            return ReviewTask.model_validate(payload["review_task"])
    return None


class ReviewTask(BaseModel):
    """The business decision the review must support, separate from query scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    decision_intent: Literal["continue", "adjust", "scale", "stop"]
    risk_focus: Literal["goal", "path", "variant", "cost"]
    business_context: str = Field(default="", max_length=1000)

    @property
    def task_id(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: date
    end: date

    @model_validator(mode="after")
    def valid_range(self):
        if self.end < self.start or (self.end - self.start).days > 365:
            raise ValueError("日期范围需按先后顺序，且不超过 366 天")
        return self


class ReviewScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    campaign_id: str = Field(min_length=1, max_length=256)
    baseline: Period
    activity: Period
    variant: str | None = Field(default=None, min_length=1, max_length=256)
    channel: str | None = Field(default=None, min_length=1, max_length=256)
    location_id: str | None = Field(default=None, min_length=1, max_length=256)
    audience: str | None = Field(default=None, min_length=1, max_length=256)
    refund_basis: Literal["after_refunds", "before_refunds"] = "after_refunds"

    @model_validator(mode="after")
    def disjoint_periods(self):
        if max(self.baseline.start, self.activity.start) <= min(
            self.baseline.end, self.activity.end
        ):
            raise ValueError("活动期与对比期不能重叠")
        return self

    @property
    def scope_id(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    @property
    def warnings(self) -> list[str]:
        result = [
            "周期差异只说明相关变化，不单独证明活动因果效果。",
            "对比期可含未绑定活动的订单；若同期间还有其他活动，不能将差额独立归因给本活动。",
            "贡献额未扣除全部经营费用，不等于净利润。",
            "活动期经营结果按支付日入期；结束后的曝光关联订单单列，不与活动期或对比期合并，不能据此推断活动因果增量。",
        ]
        if (self.baseline.end - self.baseline.start) != (self.activity.end - self.activity.start):
            result.append("两个期间天数不同，应优先比较日均指标。")
        if self.refund_basis == "before_refunds":
            result.append("当前为退款前口径，不代表最终收入。")
        return result


def _dimension_predicates(scope: ReviewScope, prefix: str = "") -> tuple[str, dict]:
    if prefix not in {"", "o.", "e."}:
        raise ValueError("unsupported column prefix")
    predicates = f"""(:variant IS NULL OR {prefix}variant=:variant)
      AND (:channel IS NULL OR {prefix}channel=:channel)
      AND (:location_id IS NULL OR {prefix}location_id=:location_id)
      AND (:audience IS NULL OR {prefix}audience=:audience)"""
    return predicates, {
        "variant": scope.variant,
        "channel": scope.channel,
        "location_id": scope.location_id,
        "audience": scope.audience,
    }


def comparison_query(scope: ReviewScope) -> tuple[str, dict]:
    revenue = (
        "net_revenue_cents"
        if scope.refund_basis == "after_refunds"
        else "gross_cents-merchant_discount_cents"
    )
    contribution = f"({revenue})-cost_cents"
    predicates, parameters = _dimension_predicates(scope)
    select = f"""SELECT :{{label}} AS period, :{{days}} AS days,
      COUNT(*) AS completed_orders, COUNT(DISTINCT user_key) AS buyer_users,
      COALESCE(SUM({revenue}),0) AS net_revenue_cents,
      COALESCE(SUM(merchant_discount_cents),0) AS merchant_discount_cents,
      COALESCE(SUM(refund_cents),0) AS refund_cents,
      COALESCE(SUM({contribution}),0) AS contribution_cents
      FROM order_metrics WHERE {predicates}
      AND ({{campaign_filter}})
      AND business_date BETWEEN :{{start}} AND :{{end}}"""
    parts = []
    for label, period in (("baseline", scope.baseline), ("activity", scope.activity)):
        campaign_filter = (
            "campaign_id=:campaign_id"
            if label == "activity"
            else "(campaign_id='' OR campaign_id=:campaign_id)"
        )
        parts.append(
            select.format(
                label=label + "_label",
                days=label + "_days",
                start=label + "_start",
                end=label + "_end",
                campaign_filter=campaign_filter,
            )
        )
        parameters.update(
            {
                label + "_label": label,
                label + "_days": (period.end - period.start).days + 1,
                label + "_start": period.start.isoformat(),
                label + "_end": period.end.isoformat(),
            }
        )
    parameters["campaign_id"] = scope.campaign_id
    return " UNION ALL ".join(parts), parameters


def timeline_query(scope: ReviewScope) -> tuple[str, dict]:
    """Return aligned daily or weekly bins, including days with no completed orders."""
    predicates, parameters = _dimension_predicates(scope, "o.")
    baseline_days = (scope.baseline.end - scope.baseline.start).days + 1
    activity_days = (scope.activity.end - scope.activity.start).days + 1
    bin_days = 1 if max(baseline_days, activity_days) <= 31 else 7
    revenue = (
        "o.net_revenue_cents"
        if scope.refund_basis == "after_refunds"
        else "o.gross_cents-o.merchant_discount_cents"
    )
    parameters.update(
        {
            "campaign_id": scope.campaign_id,
            "baseline_start": scope.baseline.start.isoformat(),
            "baseline_end": scope.baseline.end.isoformat(),
            "activity_start": scope.activity.start.isoformat(),
            "activity_end": scope.activity.end.isoformat(),
            "baseline_days": baseline_days,
            "activity_days": activity_days,
            "bin_days": bin_days,
        }
    )
    return (
        f"""WITH RECURSIVE periods(period, start_date, end_date, period_days) AS (
      SELECT 'baseline', :baseline_start, :baseline_end, :baseline_days
      UNION ALL SELECT 'activity', :activity_start, :activity_end, :activity_days
    ), bins(period, start_date, end_date, period_days, bin_index) AS (
      SELECT period, start_date, end_date, period_days, 0 FROM periods
      UNION ALL
      SELECT period, start_date, end_date, period_days, bin_index+1
      FROM bins WHERE (bin_index+1)*:bin_days < period_days
    )
    SELECT bins.period, bins.bin_index,
      date(bins.start_date, '+' || (bins.bin_index*:bin_days) || ' days') AS bin_start,
      MIN(bins.end_date, date(bins.start_date, '+' || ((bins.bin_index+1)*:bin_days-1) || ' days')) AS bin_end,
      COUNT(o.order_id) AS completed_orders,
      COALESCE(SUM({revenue}),0) AS net_revenue_cents,
      COALESCE(SUM(({revenue})-o.cost_cents),0) AS contribution_cents,
      COALESCE(SUM(o.merchant_discount_cents),0) AS merchant_discount_cents,
      COALESCE(SUM(o.refund_cents),0) AS refund_cents
    FROM bins LEFT JOIN order_metrics o
      ON o.business_date BETWEEN
        date(bins.start_date, '+' || (bins.bin_index*:bin_days) || ' days')
        AND MIN(bins.end_date, date(bins.start_date, '+' || ((bins.bin_index+1)*:bin_days-1) || ' days'))
      AND ((bins.period='activity' AND o.campaign_id=:campaign_id)
        OR (bins.period='baseline' AND o.campaign_id IN ('',:campaign_id)))
      AND {predicates}
    GROUP BY bins.period, bins.bin_index
    ORDER BY CASE bins.period WHEN 'baseline' THEN 0 ELSE 1 END, bins.bin_index""",
        parameters,
    )


def quality_query(
    scope: ReviewScope, *, matching_dimensions: tuple[str, ...] = ("variant",)
) -> tuple[str, dict]:
    """Measure path linkage, including a joint match on selected order labels."""
    allowed_dimensions = {"variant", "channel", "location_id", "audience"}
    if not matching_dimensions or set(matching_dimensions) - allowed_dimensions:
        raise ValueError("matching dimensions must be nonempty known order labels")
    selected_match_predicate = " AND ".join(
        f"e.{dimension}=o.{dimension}" for dimension in matching_dimensions
    )
    event_filters, parameters = _dimension_predicates(scope, "e.")
    order_filters, _ = _dimension_predicates(scope, "o.")
    parameters.update(
        {
            "campaign_id": scope.campaign_id,
            "activity_start": scope.activity.start.isoformat(),
            "activity_end": scope.activity.end.isoformat(),
        }
    )
    cross_filters = """(:channel IS NULL OR e.channel=:channel)
      AND (:location_id IS NULL OR e.location_id=:location_id)
      AND (:audience IS NULL OR e.audience=:audience)"""
    return (
        f"""WITH cross_variant_users AS (
      SELECT e.user_key FROM events e WHERE e.campaign_id=:campaign_id
        AND e.event_name='exposure' AND {cross_filters}
        AND date(e.event_time) BETWEEN :activity_start AND :activity_end
      GROUP BY e.user_key HAVING COUNT(DISTINCT e.variant)>1
    ), exposed AS (
      SELECT e.user_key, MIN(e.event_time) AS first_exposure_at,
        COUNT(DISTINCT e.variant) AS variant_count
      FROM events e WHERE e.campaign_id=:campaign_id
        AND e.event_name='exposure' AND {event_filters}
        AND date(e.event_time) BETWEEN :activity_start AND :activity_end
      GROUP BY e.user_key
    ), buyers AS (
      SELECT o.user_key, MIN(o.paid_at) AS first_paid_at
      FROM order_metrics o WHERE o.campaign_id=:campaign_id AND {order_filters}
        AND o.business_date BETWEEN :activity_start AND :activity_end
      GROUP BY o.user_key
    ), purchase_links AS (
      SELECT o.order_id, o.user_key,
        COALESCE(MAX(CASE WHEN e.variant=o.variant THEN 1 ELSE 0 END),0) AS variant_match,
        COALESCE(MAX(CASE WHEN e.channel=o.channel THEN 1 ELSE 0 END),0) AS channel_match,
        COALESCE(MAX(CASE WHEN e.location_id=o.location_id THEN 1 ELSE 0 END),0) AS location_match,
        COALESCE(MAX(CASE WHEN e.audience=o.audience THEN 1 ELSE 0 END),0) AS audience_match,
        COALESCE(MAX(CASE WHEN {selected_match_predicate} THEN 1 ELSE 0 END),0)
          AS selected_match
      FROM order_metrics o LEFT JOIN events e
        ON e.campaign_id=:campaign_id AND e.user_key=o.user_key
        AND e.event_name='exposure' AND e.event_time<o.paid_at
        AND date(e.event_time) BETWEEN :activity_start AND :activity_end
        AND {event_filters}
      WHERE o.campaign_id=:campaign_id AND {order_filters}
        AND o.business_date BETWEEN :activity_start AND :activity_end
      GROUP BY o.order_id, o.user_key
    )
    SELECT (SELECT COUNT(*) FROM exposed) AS exposed_users,
      (SELECT COUNT(*) FROM buyers) AS buyer_users,
      (SELECT COUNT(*) FROM cross_variant_users) AS cross_variant_exposed_users,
      (SELECT COUNT(*) FROM buyers b LEFT JOIN exposed e ON b.user_key=e.user_key
        WHERE e.user_key IS NULL OR e.first_exposure_at>=b.first_paid_at)
        AS buyers_without_prior_exposure,
      (SELECT COUNT(DISTINCT user_key) FROM purchase_links WHERE variant_match=0)
        AS buyers_without_matching_variant_exposure,
      (SELECT COUNT(DISTINCT user_key) FROM purchase_links WHERE channel_match=0)
        AS buyers_without_matching_channel_exposure,
      (SELECT COUNT(DISTINCT user_key) FROM purchase_links WHERE location_match=0)
        AS buyers_without_matching_location_exposure,
      (SELECT COUNT(DISTINCT user_key) FROM purchase_links WHERE audience_match=0)
        AS buyers_without_matching_audience_exposure,
      (SELECT COUNT(DISTINCT user_key) FROM purchase_links WHERE selected_match=0)
        AS buyers_without_matching_selected_exposure""",
        parameters,
    )


def coverage_query(scope: ReviewScope) -> tuple[str, dict]:
    """Report observed calendar coverage; rows cannot certify export completeness."""
    order_filters, parameters = _dimension_predicates(scope, "o.")
    event_filters, _ = _dimension_predicates(scope, "e.")
    parameters.update(
        {
            "campaign_id": scope.campaign_id,
            "baseline_start": scope.baseline.start.isoformat(),
            "baseline_end": scope.baseline.end.isoformat(),
            "activity_start": scope.activity.start.isoformat(),
            "activity_end": scope.activity.end.isoformat(),
            "baseline_days": (scope.baseline.end - scope.baseline.start).days + 1,
            "activity_days": (scope.activity.end - scope.activity.start).days + 1,
        }
    )
    return (
        f"""SELECT :baseline_days AS baseline_days, :activity_days AS activity_days,
      (SELECT COUNT(DISTINCT o.business_date) FROM order_metrics o
        WHERE o.campaign_id IN ('', :campaign_id) AND {order_filters}
          AND o.business_date BETWEEN :baseline_start AND :baseline_end)
        AS baseline_days_with_orders,
      (SELECT COUNT(DISTINCT o.business_date) FROM order_metrics o
        WHERE o.campaign_id=:campaign_id AND {order_filters}
          AND o.business_date BETWEEN :activity_start AND :activity_end)
        AS activity_days_with_orders,
      (SELECT COUNT(DISTINCT date(e.event_time)) FROM events e
        WHERE e.campaign_id=:campaign_id AND e.event_name='exposure'
          AND {event_filters}
          AND date(e.event_time) BETWEEN :activity_start AND :activity_end)
        AS activity_days_with_exposure,
      (SELECT MAX(o.business_date) FROM order_metrics o
        WHERE o.campaign_id=:campaign_id AND {order_filters}
          AND o.business_date BETWEEN :activity_start AND :activity_end)
        AS last_observed_activity_order_date""",
        parameters,
    )


def attribution_tail_query(scope: ReviewScope, attribution_days: int) -> tuple[str, dict]:
    """Keep post-period, exposure-linked purchases separate from activity outcomes.

    This is a declared observational attribution rule over tagged orders, not
    an estimate of incremental impact. The window is bounded by the campaign
    end and first prior exposure with the same assignment labels as each order.
    """
    if not 0 <= attribution_days <= 90:
        raise ValueError("attribution_days out of range")
    event_filters, parameters = _dimension_predicates(scope, "e.")
    order_filters, _ = _dimension_predicates(scope, "o.")
    revenue = (
        "o.net_revenue_cents"
        if scope.refund_basis == "after_refunds"
        else "o.gross_cents-o.merchant_discount_cents"
    )
    parameters.update(
        {
            "campaign_id": scope.campaign_id,
            "activity_start": scope.activity.start.isoformat(),
            "activity_end": scope.activity.end.isoformat(),
            "attribution_days": attribution_days,
        }
    )
    return (
        f"""WITH matched_orders AS (
      SELECT o.*,
        (SELECT MIN(e.event_time) FROM events e
          WHERE e.campaign_id=:campaign_id AND e.event_name='exposure'
            AND e.user_key=o.user_key AND e.event_time<o.paid_at
            AND e.variant=o.variant AND e.channel=o.channel
            AND e.location_id=o.location_id AND e.audience=o.audience
            AND {event_filters}
            AND date(e.event_time) BETWEEN :activity_start AND :activity_end
        ) AS first_matching_exposure_at
      FROM order_metrics o
      WHERE o.campaign_id=:campaign_id AND {order_filters}
        AND o.business_date>:activity_end
        AND o.business_date<=date(:activity_end,'+' || :attribution_days || ' days')
    )
    SELECT COUNT(o.order_id) AS completed_orders,
      COUNT(DISTINCT o.user_key) AS buyer_users,
      COALESCE(SUM({revenue}),0) AS net_revenue_cents,
      COALESCE(SUM(({revenue})-o.cost_cents),0) AS contribution_cents,
      (SELECT COUNT(*) FROM matched_orders) AS candidate_orders,
      (SELECT COUNT(*) FROM matched_orders
        WHERE first_matching_exposure_at IS NULL)
        AS orders_without_matching_exposure,
      (SELECT COUNT(*) FROM matched_orders
        WHERE first_matching_exposure_at IS NOT NULL
          AND datetime(paid_at)>datetime(first_matching_exposure_at,'+' || :attribution_days || ' days'))
        AS orders_outside_exposure_window
    FROM matched_orders o
    WHERE o.first_matching_exposure_at IS NOT NULL
      AND datetime(o.paid_at)<=datetime(o.first_matching_exposure_at,'+' || :attribution_days || ' days')""",
        parameters,
    )


def _scoped_journey_query(
    scope: ReviewScope,
    dimension: Literal["variant", "channel", "location_id", "audience"],
    *,
    value_alias: str = "dimension_value",
    contribution_alias: str = "contribution_cents",
) -> tuple[str, dict]:
    """Aggregate distinct people at the selected grain, within the activity period.

    The static campaign_funnel view is useful for exploration but cannot be summed
    across dates or other dimensions without double-counting people.
    """
    if dimension not in {"variant", "channel", "location_id", "audience"}:
        raise ValueError("unsupported diagnostic dimension")
    if value_alias not in {"variant", "dimension_value"} or contribution_alias not in {
        "contribution_cents",
        "order_contribution_cents",
    }:
        raise ValueError("unsupported output alias")
    predicates, parameters = _dimension_predicates(scope)
    event_predicates, _ = _dimension_predicates(scope, "e.")
    parameters.update(
        {
            "campaign_id": scope.campaign_id,
            "activity_start": scope.activity.start.isoformat(),
            "activity_end": scope.activity.end.isoformat(),
        }
    )
    revenue = (
        "net_revenue_cents"
        if scope.refund_basis == "after_refunds"
        else "gross_cents-merchant_discount_cents"
    )
    query = f"""WITH event_first AS (
      SELECT {dimension} AS dimension_value, user_key,
        MIN(CASE WHEN event_name='exposure' THEN event_time END) AS exposure_at
      FROM events WHERE campaign_id=:campaign_id AND {predicates}
        AND date(event_time) BETWEEN :activity_start AND :activity_end
      GROUP BY {dimension}, user_key
    ), stage_landing AS (
      SELECT f.*, (SELECT MIN(e.event_time) FROM events e
        WHERE e.campaign_id=:campaign_id AND e.{dimension}=f.dimension_value
          AND e.user_key=f.user_key AND e.event_name='landing_view'
          AND e.event_time>f.exposure_at AND {event_predicates}
          AND date(e.event_time) BETWEEN :activity_start AND :activity_end
      ) AS valid_landing_at FROM event_first f
    ), stage_claim AS (
      SELECT f.*, (SELECT MIN(e.event_time) FROM events e
        WHERE e.campaign_id=:campaign_id AND e.{dimension}=f.dimension_value
          AND e.user_key=f.user_key AND e.event_name='offer_claim'
          AND e.event_time>f.valid_landing_at AND {event_predicates}
          AND date(e.event_time) BETWEEN :activity_start AND :activity_end
      ) AS valid_claim_at FROM stage_landing f
    ), stage_activation AS (
      SELECT f.*, (SELECT MIN(e.event_time) FROM events e
        WHERE e.campaign_id=:campaign_id AND e.{dimension}=f.dimension_value
          AND e.user_key=f.user_key AND e.event_name='activation'
          AND e.event_time>f.valid_claim_at AND {event_predicates}
          AND date(e.event_time) BETWEEN :activity_start AND :activity_end
      ) AS valid_activation_at FROM stage_claim f
    ), event_users AS (
      SELECT dimension_value,
        COUNT(CASE WHEN exposure_at IS NOT NULL THEN 1 END) AS exposed_users,
        COUNT(CASE WHEN valid_landing_at IS NOT NULL THEN 1 END) AS landing_users,
        COUNT(CASE WHEN valid_claim_at IS NOT NULL THEN 1 END) AS claim_users,
        COUNT(CASE WHEN valid_activation_at IS NOT NULL THEN 1 END) AS activated_users
      FROM stage_activation GROUP BY dimension_value
    ), buyers AS (
      SELECT {dimension} AS dimension_value,
        COUNT(DISTINCT user_key) AS buyer_users,
        COUNT(*) AS completed_orders,
        COALESCE(SUM({revenue}),0) AS net_revenue_cents,
        COALESCE(SUM(({revenue})-cost_cents),0) AS contribution_cents
      FROM order_metrics WHERE campaign_id=:campaign_id AND {predicates}
        AND business_date BETWEEN :activity_start AND :activity_end
      GROUP BY {dimension}
    ), path_buyers AS (
      SELECT o.{dimension} AS dimension_value, COUNT(DISTINCT o.user_key) AS path_buyer_users
      FROM order_metrics o WHERE o.campaign_id=:campaign_id
        AND (:variant IS NULL OR o.variant=:variant)
        AND (:channel IS NULL OR o.channel=:channel)
        AND (:location_id IS NULL OR o.location_id=:location_id)
        AND (:audience IS NULL OR o.audience=:audience)
        AND o.business_date BETWEEN :activity_start AND :activity_end
        AND EXISTS (SELECT 1 FROM stage_activation a
          WHERE a.dimension_value=o.{dimension} AND a.user_key=o.user_key
            AND a.valid_activation_at<o.paid_at)
      GROUP BY o.{dimension}
    ), keys AS (
      SELECT dimension_value FROM event_users
      UNION SELECT dimension_value FROM buyers
    )
    SELECT keys.dimension_value AS {value_alias},
      COALESCE(e.exposed_users,0) AS exposed_users,
      COALESCE(e.landing_users,0) AS landing_users,
      COALESCE(e.claim_users,0) AS claim_users,
      COALESCE(e.activated_users,0) AS activated_users,
      COALESCE(b.buyer_users,0) AS buyer_users,
      COALESCE(pb.path_buyer_users,0) AS path_buyer_users,
      COALESCE(e.activated_users,0)-COALESCE(pb.path_buyer_users,0)
        AS activated_without_path_purchase_users,
      CASE WHEN COALESCE(e.activated_users,0)>0
        THEN 1.0*COALESCE(pb.path_buyer_users,0)/e.activated_users
        ELSE NULL END AS activation_to_path_purchase_rate,
      COALESCE(b.completed_orders,0) AS completed_orders,
      COALESCE(b.net_revenue_cents,0) AS net_revenue_cents,
      COALESCE(b.contribution_cents,0) AS {contribution_alias},
      CASE WHEN COALESCE(e.exposed_users,0)>0
        THEN 1.0*COALESCE(b.buyer_users,0)/e.exposed_users ELSE NULL END AS conversion_rate
    FROM keys
    LEFT JOIN event_users e ON keys.dimension_value=e.dimension_value
    LEFT JOIN buyers b ON keys.dimension_value=b.dimension_value
    LEFT JOIN path_buyers pb ON keys.dimension_value=pb.dimension_value"""
    return query, parameters


def funnel_query(scope: ReviewScope) -> tuple[str, dict]:
    query, parameters = _scoped_journey_query(scope, "variant", value_alias="variant")
    return f"{query} ORDER BY keys.dimension_value", parameters


def cost_query(scope: ReviewScope) -> tuple[str, dict]:
    parameters = {
        "campaign_id": scope.campaign_id,
        "variant": scope.variant,
        "channel": scope.channel,
        "activity_start": scope.activity.start.isoformat(),
        "activity_end": scope.activity.end.isoformat(),
    }
    return (
        """SELECT variant, channel, COALESCE(SUM(amount_cents),0) AS activity_cost_cents
      FROM costs WHERE campaign_id=:campaign_id
      AND business_date BETWEEN :activity_start AND :activity_end
      AND (:variant IS NULL OR variant=:variant OR variant='all')
      AND (:channel IS NULL OR channel=:channel OR channel='all')
      GROUP BY variant, channel ORDER BY variant, channel""",
        parameters,
    )


def breakdown_query(
    scope: ReviewScope,
    dimension: Literal["variant", "channel", "location_id", "audience"],
) -> tuple[str, dict]:
    """Build an allowlisted path-and-value breakdown for one confirmed scope."""
    query, parameters = _scoped_journey_query(
        scope,
        dimension,
        contribution_alias="order_contribution_cents",
    )
    query = query.replace(
        "SELECT keys.dimension_value AS dimension_value,",
        "SELECT keys.dimension_value AS dimension_value, COUNT(*) OVER() AS total_groups,",
        1,
    )
    return f"{query} ORDER BY exposed_users DESC, keys.dimension_value LIMIT 50", parameters
