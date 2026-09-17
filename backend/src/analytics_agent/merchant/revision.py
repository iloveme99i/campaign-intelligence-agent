"""Deterministic audit for conclusions recalculated under a changed review scope."""

from __future__ import annotations

import hashlib
from typing import Any

import orjson

SCOPE_FIELDS = (
    ("campaign_id", "活动"),
    ("baseline", "对比期"),
    ("activity", "活动期"),
    ("variant", "活动方案"),
    ("channel", "渠道"),
    ("location_id", "经营点"),
    ("audience", "目标人群"),
    ("refund_basis", "收入口径"),
)

METRIC_FIELDS = (
    ("completed_orders", "完成订单", "count"),
    ("buyer_users", "购买用户", "count"),
    ("net_revenue_cents", "净收入", "cents"),
    ("merchant_discount_cents", "商家优惠", "cents"),
    ("contribution_cents", "订单贡献额", "cents"),
)

BUSINESS_VALUES = {
    "after_refunds": "退款后",
    "before_refunds": "退款前",
    "login_gift": "登录礼包",
    "staged_missions": "阶段任务",
    "single_discount": "单件直降",
    "live_bundle": "直播组合包",
    "single_coupon": "单券承接",
    "content_coupon": "内容与券联动",
    "wechat_game": "微信游戏",
    "game_client": "游戏客户端",
    "wegame": "WeGame",
}


def _payload(message: Any) -> dict:
    value = message.payload
    return orjson.loads(value) if isinstance(value, str) else value


def _period(value: Any) -> str:
    if not isinstance(value, dict):
        return "未限定"
    return f"{value.get('start', '—')} 至 {value.get('end', '—')}"


def _scope_value(key: str, value: Any) -> str:
    if key in {"baseline", "activity"}:
        return _period(value)
    if value in (None, ""):
        return "全部"
    return BUSINESS_VALUES.get(str(value), str(value))


def _activity_row(evidence: dict) -> dict:
    return next(
        (row for row in evidence.get("rows", []) if row.get("period") == "activity"),
        {},
    )


def _completed_scope_turns(messages: list[Any]) -> list[dict]:
    turns: list[dict] = []
    current: dict | None = None
    for message in messages:
        payload = _payload(message)
        if message.role == "user" and message.event_type == "TEXT":
            scope = payload.get("review_scope")
            current = (
                {
                    "scope": scope,
                    "scope_id": payload.get("scope_id"),
                    "evidence": None,
                    "answer": "",
                    "decision": None,
                }
                if isinstance(scope, dict)
                else None
            )
            if current is not None:
                turns.append(current)
        elif current is not None and message.event_type == "SQL":
            if payload.get("scope_confirmed") is True:
                current["evidence"] = payload
        elif current is not None and message.event_type == "COMPLETE":
            current["answer"] = payload.get("text", "")
        elif current is not None and message.event_type == "DECISION":
            current["decision"] = payload
    return [turn for turn in turns if turn["evidence"] and turn["answer"]]


def summarize_scope_revision(messages: list[Any]) -> dict:
    """Compare the latest two completed evidence turns without asking the model."""

    turns = _completed_scope_turns(messages)
    if len(turns) < 2:
        return {
            "status": "single_scope",
            "completed_scope_count": len(turns),
            "changed_fields": [],
            "metric_changes": [],
            "old_conclusion_superseded": False,
            "previous_decision_superseded": False,
        }

    current = turns[-1]
    previous = next(
        (turn for turn in reversed(turns[:-1]) if turn["scope_id"] != current["scope_id"]),
        None,
    )
    if previous is None:
        return {
            "status": "single_scope",
            "completed_scope_count": len(turns),
            "changed_fields": [],
            "metric_changes": [],
            "old_conclusion_superseded": False,
            "previous_decision_superseded": False,
        }

    changed_fields = []
    for key, label in SCOPE_FIELDS:
        before = previous["scope"].get(key)
        after = current["scope"].get(key)
        if before != after:
            changed_fields.append(
                {
                    "key": key,
                    "label": label,
                    "previous": _scope_value(key, before),
                    "current": _scope_value(key, after),
                }
            )

    previous_row = _activity_row(previous["evidence"])
    current_row = _activity_row(current["evidence"])
    metric_changes = []
    for key, label, unit in METRIC_FIELDS:
        before = previous_row.get(key)
        after = current_row.get(key)
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            continue
        delta = after - before
        metric_changes.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "previous": before,
                "current": after,
                "delta": delta,
                "change_rate": delta / abs(before) if before else None,
            }
        )

    return {
        "status": "revised",
        "completed_scope_count": len(turns),
        "changed_fields": changed_fields,
        "metric_changes": metric_changes,
        "old_conclusion_superseded": True,
        "previous_decision_superseded": previous["decision"] is not None,
        "reason": "复盘口径发生变化，本轮已重新计算；上一口径的数字结论不能沿用。",
        "previous_scope_id": previous["scope_id"],
        "current_scope_id": current["scope_id"],
        "previous_answer_sha256": hashlib.sha256(previous["answer"].encode()).hexdigest(),
        "current_answer_sha256": hashlib.sha256(current["answer"].encode()).hexdigest(),
    }
