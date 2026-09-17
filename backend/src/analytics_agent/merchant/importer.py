"""Validate campaign exports before publishing an immutable SQLite snapshot."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = {
    "campaigns": [
        "campaign_id",
        "campaign_name",
        "objective",
        "start_date",
        "end_date",
        "primary_metric",
        "target_value",
        "attribution_days",
    ],
    "events": [
        "event_id",
        "user_key",
        "event_time",
        "event_name",
        "campaign_id",
        "variant",
        "channel",
        "location_id",
        "audience",
    ],
    "orders": [
        "order_id",
        "user_key",
        "paid_at",
        "campaign_id",
        "variant",
        "channel",
        "location_id",
        "audience",
        "status",
        "gross_cents",
        "merchant_discount_cents",
        "platform_discount_cents",
        "refund_cents",
        "cost_cents",
    ],
    "costs": [
        "cost_id",
        "campaign_id",
        "business_date",
        "variant",
        "channel",
        "cost_type",
        "amount_cents",
    ],
}

INCREMENTALITY_FIELDS = [
    "panel_unit",
    "campaign_id",
    "period",
    "metric",
    "treated",
    "post",
    "outcome",
    "interference_reviewed",
]
TABLE_FIELDS = {**FIELDS, "incrementality": INCREMENTALITY_FIELDS}

INTEGER_FIELDS = {
    "attribution_days",
    "gross_cents",
    "merchant_discount_cents",
    "platform_discount_cents",
    "refund_cents",
    "cost_cents",
    "amount_cents",
}
OPTIONAL_FIELDS = {"variant", "channel", "location_id", "audience"}
EVENT_NAMES = {"exposure", "landing_view", "offer_claim", "activation"}
ORDER_STATUSES = {"completed", "cancelled"}
PRIMARY_METRICS = {"conversion_rate", "completed_orders", "net_revenue", "roi"}

VIEWS_SQL = """
CREATE VIEW order_metrics AS
SELECT *, date(paid_at) AS business_date,
       gross_cents-merchant_discount_cents-platform_discount_cents AS customer_paid_cents,
       gross_cents-merchant_discount_cents-refund_cents AS net_revenue_cents,
       gross_cents-merchant_discount_cents-refund_cents-cost_cents AS contribution_cents
FROM orders
WHERE status='completed';

CREATE VIEW campaign_funnel AS
WITH event_users AS (
  SELECT campaign_id, variant, channel, location_id, audience,
    COUNT(DISTINCT CASE WHEN event_name='exposure' THEN user_key END) exposed_users,
    COUNT(DISTINCT CASE WHEN event_name='landing_view' THEN user_key END) landing_users,
    COUNT(DISTINCT CASE WHEN event_name='offer_claim' THEN user_key END) claim_users,
    COUNT(DISTINCT CASE WHEN event_name='activation' THEN user_key END) activated_users
  FROM events GROUP BY campaign_id, variant, channel, location_id, audience
), buyers AS (
  SELECT campaign_id, variant, channel, location_id, audience,
    COUNT(DISTINCT user_key) buyer_users, COUNT(*) completed_orders,
    SUM(net_revenue_cents) net_revenue_cents, SUM(contribution_cents) contribution_cents
  FROM order_metrics GROUP BY campaign_id, variant, channel, location_id, audience
)
SELECT e.*, COALESCE(b.buyer_users,0) buyer_users,
  COALESCE(b.completed_orders,0) completed_orders,
  COALESCE(b.net_revenue_cents,0) net_revenue_cents,
  COALESCE(b.contribution_cents,0) contribution_cents
FROM event_users e LEFT JOIN buyers b
ON e.campaign_id=b.campaign_id AND e.variant=b.variant AND e.channel=b.channel
AND e.location_id=b.location_id AND e.audience=b.audience;
"""


class ImportValidationError(ValueError):
    pass


def _parse_iso_date(value: str, prefix: str) -> None:
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ImportValidationError(f"{prefix}: 日期必须为 YYYY-MM-DD") from None


def _parse_local_time(value: str, prefix: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None or "T" not in value:
            raise ValueError()
    except ValueError:
        raise ImportValidationError(f"{prefix}: 时间必须为本地时间 YYYY-MM-DDTHH:MM:SS") from None


def parse_exports(files: dict[str, bytes]) -> dict[str, list[dict]]:
    supplied = set(files)
    if not set(FIELDS).issubset(supplied) or not supplied.issubset(TABLE_FIELDS):
        raise ImportValidationError(
            "需要 campaigns/events/orders/costs 四份 CSV；incrementality 为可选的增量识别面板"
        )
    if sum(map(len, files.values())) > 20_000_000:
        raise ImportValidationError("文件合计不能超过 20 MB")
    tables: dict[str, list[dict]] = {}
    for name, fields in TABLE_FIELDS.items():
        if name not in files:
            tables[name] = []
            continue
        try:
            reader = csv.DictReader(io.StringIO(files[name].decode("utf-8-sig")), strict=True)
            if reader.fieldnames != fields:
                raise ImportValidationError(f"{name}: 表头及顺序应为 {','.join(fields)}")
            rows, seen = [], set()
            for line, row in enumerate(reader, 2):
                prefix = f"{name}:{line}"
                if None in row or any(value is None for value in row.values()):
                    raise ImportValidationError(f"{prefix}: 列数不匹配")
                for field, value in row.items():
                    optional = field in OPTIONAL_FIELDS or (
                        name == "orders" and field == "campaign_id"
                    )
                    if len(value) > 256 or (not value.strip() and not optional):
                        raise ImportValidationError(f"{prefix}: {field} 为空或过长")
                    if field in INTEGER_FIELDS:
                        if not re.fullmatch(r"[0-9]{1,10}", value):
                            raise ImportValidationError(f"{prefix}: {field} 必须为非负整数")
                        row[field] = int(value)
                key = (
                    (
                        row["panel_unit"],
                        row["campaign_id"],
                        row["period"],
                        row["metric"],
                    )
                    if name == "incrementality"
                    else row[fields[0]]
                )
                if key in seen:
                    raise ImportValidationError(f"{prefix}: 主键重复")
                seen.add(key)
                if name == "campaigns":
                    _parse_iso_date(row["start_date"], prefix)
                    _parse_iso_date(row["end_date"], prefix)
                    if row["end_date"] < row["start_date"]:
                        raise ImportValidationError(f"{prefix}: 活动结束日期早于开始日期")
                    if row["primary_metric"] not in PRIMARY_METRICS:
                        raise ImportValidationError(f"{prefix}: 不支持的主指标")
                    try:
                        target = Decimal(row["target_value"])
                        if not target.is_finite() or target < 0:
                            raise InvalidOperation()
                    except InvalidOperation:
                        raise ImportValidationError(
                            f"{prefix}: target_value 必须为非负数字"
                        ) from None
                    if not 0 <= row["attribution_days"] <= 90:
                        raise ImportValidationError(f"{prefix}: attribution_days 应在 0 至 90 之间")
                elif name == "events":
                    _parse_local_time(row["event_time"], prefix)
                    if row["event_name"] not in EVENT_NAMES:
                        raise ImportValidationError(f"{prefix}: 不支持的事件名")
                elif name == "orders":
                    _parse_local_time(row["paid_at"], prefix)
                    if row["status"] not in ORDER_STATUSES:
                        raise ImportValidationError(f"{prefix}: 非法订单状态")
                    if (
                        row["merchant_discount_cents"] + row["platform_discount_cents"]
                        > row["gross_cents"]
                    ):
                        raise ImportValidationError(f"{prefix}: 优惠超过订单原价")
                    if (
                        row["refund_cents"]
                        > row["gross_cents"]
                        - row["merchant_discount_cents"]
                        - row["platform_discount_cents"]
                    ):
                        raise ImportValidationError(f"{prefix}: 退款超过顾客实付")
                elif name == "costs":
                    _parse_iso_date(row["business_date"], prefix)
                elif name == "incrementality":
                    _parse_iso_date(row["period"], prefix)
                    if row["metric"] not in {
                        "completed_orders",
                        "buyer_users",
                        "net_revenue_cents",
                        "contribution_cents",
                    }:
                        raise ImportValidationError(f"{prefix}: incrementality metric 不受支持")
                    for field in ("treated", "post", "interference_reviewed"):
                        if row[field] not in {"0", "1"}:
                            raise ImportValidationError(f"{prefix}: {field} 必须为 0 或 1")
                        row[field] = int(row[field])
                    try:
                        outcome = Decimal(row["outcome"])
                        if not outcome.is_finite() or outcome < 0:
                            raise InvalidOperation()
                    except InvalidOperation:
                        raise ImportValidationError(f"{prefix}: outcome 必须为非负有限数") from None
                    row["outcome"] = float(outcome)
                rows.append(row)
                if len(rows) > 100_000:
                    raise ImportValidationError(f"{name}: 最多 100000 行")
            tables[name] = rows
        except (UnicodeError, csv.Error) as exc:
            raise ImportValidationError(f"{name}: 需要合法 UTF-8 CSV") from exc

    if not tables["campaigns"]:
        raise ImportValidationError("campaigns: 至少需要一个活动")
    campaign_ids = {row["campaign_id"] for row in tables["campaigns"]}
    campaigns = {row["campaign_id"]: row for row in tables["campaigns"]}
    exposures: set[tuple[str, str, str]] = set()
    for name in ("events", "orders", "costs", "incrementality"):
        for line, row in enumerate(tables[name], 2):
            if row["campaign_id"] and row["campaign_id"] not in campaign_ids:
                raise ImportValidationError(f"{name}:{line}: campaign_id 不存在")
            if name == "events":
                event_date = row["event_time"][:10]
                campaign = campaigns[row["campaign_id"]]
                if not campaign["start_date"] <= event_date <= campaign["end_date"]:
                    raise ImportValidationError(f"{name}:{line}: 事件不在活动日期内")
                if row["event_name"] == "exposure":
                    assignment = (row["campaign_id"], row["user_key"], row["variant"])
                    if assignment in exposures:
                        raise ImportValidationError(f"{name}:{line}: 同一用户实验曝光重复")
                    exposures.add(assignment)
    assignments: dict[tuple[str, str, str], int] = {}
    period_flags: dict[tuple[str, str, str], tuple[int, int]] = {}
    for line, row in enumerate(tables["incrementality"], 2):
        unit_key = (row["campaign_id"], row["metric"], row["panel_unit"])
        if unit_key in assignments and assignments[unit_key] != row["treated"]:
            raise ImportValidationError(
                f"incrementality:{line}: treatment assignment changes over time"
            )
        assignments[unit_key] = row["treated"]
        period_key = (row["campaign_id"], row["metric"], row["period"])
        period_state = (row["post"], row["interference_reviewed"])
        if period_key in period_flags and period_flags[period_key] != period_state:
            raise ImportValidationError(
                f"incrementality:{line}: post/interference flag is inconsistent within period"
            )
        period_flags[period_key] = period_state
    return tables


def import_snapshot(files: dict[str, bytes], directory: Path) -> dict:
    tables = parse_exports(files)
    digest = hashlib.sha256()
    # The immutable database layout is part of the snapshot identity. A new
    # index/schema must never silently reuse a prior, slower physical snapshot.
    digest.update(b"merchant-snapshot-schema-v3\0")
    for name in TABLE_FIELDS:
        digest.update(name.encode() + b"\0" + files.get(name, b"") + b"\0")
    snapshot_id = digest.hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{snapshot_id}.db"
    descriptor, temporary = tempfile.mkstemp(prefix="import-", suffix=".db", dir=directory)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(temporary)) as conn, conn:
            for name, fields in TABLE_FIELDS.items():
                if name == "incrementality":
                    conn.execute(
                        "CREATE TABLE incrementality ("
                        "panel_unit TEXT, campaign_id TEXT, period TEXT, metric TEXT, "
                        "treated INTEGER, post INTEGER, outcome REAL, "
                        "interference_reviewed INTEGER, "
                        "PRIMARY KEY(panel_unit,campaign_id,period,metric))"
                    )
                    conn.executemany(
                        "INSERT INTO incrementality VALUES (?,?,?,?,?,?,?,?)",
                        [[row[field] for field in fields] for row in tables[name]],
                    )
                    conn.execute(
                        "CREATE INDEX incrementality_campaign_idx ON incrementality"
                        "(campaign_id,metric,period,treated,post)"
                    )
                    continue
                columns = ",".join(
                    f"{field} {'INTEGER' if field in INTEGER_FIELDS else 'TEXT'}"
                    + (" PRIMARY KEY" if field == fields[0] else "")
                    for field in fields
                )
                conn.execute(f"CREATE TABLE {name} ({columns})")
                conn.executemany(
                    f"INSERT INTO {name} VALUES ({','.join('?' for _ in fields)})",
                    [[row[field] for field in fields] for row in tables[name]],
                )
                if name != "campaigns":
                    conn.execute(f"CREATE INDEX {name}_campaign_idx ON {name}(campaign_id)")
                if name == "events":
                    conn.execute(
                        "CREATE INDEX events_path_idx ON events"
                        "(campaign_id,user_key,event_name,event_time,variant,channel,location_id,audience)"
                    )
            conn.executescript(VIEWS_SQL)
        try:
            os.link(temporary, target)
        except FileExistsError:
            pass
    finally:
        os.unlink(temporary)
    return {
        "snapshot_id": snapshot_id,
        "path": str(target),
        "row_counts": {key: len(value) for key, value in tables.items()},
        "metric_version": "v0.8",
    }
