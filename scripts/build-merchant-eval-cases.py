#!/usr/bin/env python3
"""Build the deterministic 54-case Campaign Intelligence evaluation matrix."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

CAMPAIGNS = (
    {
        "id": "byte_full_funnel_sale",
        "name": "字节系 · 全域大促",
        "start": "2026-06-12",
        "end": "2026-06-18",
        "variant": "直播组合包",
        "channel": "live_room",
        "location_id": "regional_store",
        "audience": "returning_customer",
    },
    {
        "id": "tencent_game_return_ops",
        "name": "腾讯游戏 · 回流任务季",
        "start": "2026-07-16",
        "end": "2026-07-22",
        "variant": "阶段任务",
        "channel": "wechat_game",
        "location_id": "server_south",
        "audience": "returning_60d",
    },
    {
        "id": "tencent_private_retail",
        "name": "腾讯零售 · 私域联动",
        "start": "2026-08-06",
        "end": "2026-08-12",
        "variant": "内容与券联动",
        "channel": "video_account",
        "location_id": "store_south",
        "audience": "member_dormant",
    },
)

INTENTS = {
    "continue": ("判断当前机制是否值得继续", ("目标", "路径", "成本", "继续条件")),
    "adjust": ("定位下一轮优先调整的对象", ("目标", "最低承接节点", "调整对象", "验证条件")),
    "scale": ("判断当前证据是否支持扩大覆盖", ("目标", "贡献额", "扩大条件", "护栏")),
    "stop": ("判断是否应当及时停止投入", ("目标", "贡献额", "停止条件", "风险")),
}

RISKS = {
    "goal": (
        "先核对主目标是否真实达成",
        ("variant", "channel", "location_id", "audience"),
    ),
    "path": (
        "先定位用户路径中的最低承接节点",
        ("variant", "channel", "location_id", "audience"),
    ),
    "variant": ("先判断方案差异是否足够可信", ("variant",)),
    "cost": ("先判断经营结果能否覆盖活动投入", ("variant", "channel", "location_id")),
}


def _scope(campaign: dict, **filters: str | None) -> dict:
    start = date.fromisoformat(campaign["start"])
    end = date.fromisoformat(campaign["end"])
    days = (end - start).days + 1
    baseline_end = start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=days - 1)
    return {
        "campaign_id": campaign["id"],
        "baseline": {"start": baseline_start.isoformat(), "end": baseline_end.isoformat()},
        "activity": {"start": campaign["start"], "end": campaign["end"]},
        "variant": filters.get("variant"),
        "channel": filters.get("channel"),
        "location_id": filters.get("location_id"),
        "audience": filters.get("audience"),
        "refund_basis": "after_refunds",
    }


def _case(
    campaign: dict,
    intent: str,
    risk: str,
    *,
    suffix: str = "full",
    business_context: str = "",
    filters: dict[str, str] | None = None,
) -> dict:
    intent_prompt, topics = INTENTS[intent]
    risk_prompt, allowed = RISKS[risk]
    scope = _scope(campaign, **(filters or {}))
    qualifier = f"；已知限制：{business_context}" if business_context else ""
    return {
        "id": f"{campaign['id']}__{intent}__{risk}__{suffix}",
        "data_origin": "deterministic_synthetic_fixture",
        "prompt": f"复盘「{campaign['name']}」：{intent_prompt}，{risk_prompt}{qualifier}。",
        "scope": scope,
        "task": {
            "decision_intent": intent,
            "risk_focus": risk,
            "business_context": business_context,
        },
        "oracle": {
            "first_tool": "compare_periods",
            "required_diagnostic": "diagnose_dimension",
            "allowed_diagnostics": list(allowed),
            "max_diagnostics": 3,
            "required_answer_topics": list(topics),
            "required_quality_status": "passed",
            "must_state_causal_limit": True,
        },
    }


def build_cases() -> list[dict]:
    cases = [
        _case(campaign, intent, risk)
        for campaign in CAMPAIGNS
        for intent in INTENTS
        for risk in RISKS
    ]
    filtered = (
        (CAMPAIGNS[0], "scale", "cost", "channel", "live_room", "直播间共享投放费用尚未完成渠道分摊"),
        (CAMPAIGNS[0], "adjust", "path", "variant", "直播组合包", "组合包承接页在活动中途更换过一次"),
        (CAMPAIGNS[1], "continue", "path", "channel", "wechat_game", "微信游戏入口有一日登录埋点延迟"),
        (CAMPAIGNS[1], "stop", "variant", "audience", "returning_60d", "60 日回流用户不能与 30 日人群混合解释"),
        (CAMPAIGNS[2], "adjust", "cost", "location_id", "store_south", "南区门店存在阶段性缺货"),
        (CAMPAIGNS[2], "continue", "goal", "audience", "member_dormant", "沉默会员的跨渠道归属尚未完成核对"),
    )
    for campaign, intent, risk, dimension, value, context in filtered:
        cases.append(
            _case(
                campaign,
                intent,
                risk,
                suffix=f"filtered_{dimension}",
                business_context=context,
                filters={dimension: value},
            )
        )
    return cases


def main() -> None:
    target = Path(__file__).parents[1] / "evals" / "merchant_review_cases.jsonl"
    cases = build_cases()
    if len(cases) != 54 or len({case["id"] for case in cases}) != len(cases):
        raise SystemExit("evaluation matrix must contain 54 unique cases")
    target.write_text(
        "".join(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n" for case in cases),
        encoding="utf-8",
    )
    print(f"wrote {len(cases)} cases to {target}")


if __name__ == "__main__":
    main()
