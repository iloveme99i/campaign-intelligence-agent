"""Deterministic exports of persisted turns, with no additional model invocation."""

from __future__ import annotations

import re
from typing import Any

import orjson

LIMITATIONS = [
    "贡献额未扣除全部经营费用，不等于净利润。",
    "退款按原交易时间计入，不是到账日现金流口径。",
    "活动前后差异不能单独证明活动带来的因果效果。",
    "按轮次保留原始结论；口径变更前后的回答不能混用。",
    "活动期订单按支付日入期；结束后的订单须匹配同用户及同方案、渠道、经营点、人群的先前曝光，按配置窗口单列，不能据此证明因果增量。",
]


def build_report(conversation: Any, messages: list, *, running: bool = False) -> dict:
    from analytics_agent.merchant.revision import summarize_scope_revision

    turns: list[dict] = []
    current: dict | None = None
    previous_scope_id = None
    for message in messages:
        payload = (
            orjson.loads(message.payload) if isinstance(message.payload, str) else message.payload
        )
        if message.role == "user" and message.event_type == "TEXT":
            current = {
                "number": len(turns) + 1,
                "question": payload.get("text", ""),
                "answer": "",
                "status": "incomplete",
                "evidence": [],
                "errors": [],
                "decision": None,
                "outcome": None,
                "_chunks": [],
                "review_scope": payload.get("review_scope"),
                "scope_id": payload.get("scope_id"),
                "scope_source": payload.get("scope_source"),
                "review_task": payload.get("review_task"),
                "task_id": payload.get("task_id"),
                "task_source": payload.get("task_source"),
                "supersedes_scope_id": previous_scope_id
                if payload.get("scope_id") and payload.get("scope_id") != previous_scope_id
                else None,
            }
            if payload.get("scope_id"):
                previous_scope_id = payload["scope_id"]
            turns.append(current)
        elif current is not None and message.event_type == "DECISION":
            current["decision"] = dict(payload)
        elif current is not None and message.event_type == "OUTCOME":
            current["outcome"] = dict(payload)
        elif current is not None and message.role == "assistant":
            if message.event_type == "TEXT":
                current["_chunks"].append(payload.get("text", ""))
            elif message.event_type == "SQL":
                current["evidence"].append(dict(payload))
            elif message.event_type == "ERROR" or (
                message.event_type == "TOOL_RESULT" and payload.get("is_error")
            ):
                current["errors"].append(
                    payload.get("error", payload.get("result", "unknown_error"))
                )
            elif message.event_type == "COMPLETE":
                current["answer"] = payload.get("text", "")
                # COMPLETE also follows failures upstream; don't treat it as success by itself.
                if current["answer"]:
                    current["status"] = (
                        "completed_with_errors" if current["errors"] else "completed"
                    )
    for turn in turns:
        chunks = turn.pop("_chunks")
        if not turn["answer"]:
            turn["answer"] = "".join(chunks)
    if running and turns:
        turns[-1]["status"] = "running"
    known_ids = {
        evidence_id
        for turn in turns
        for entry in turn["evidence"]
        for evidence_id in [
            entry.get("evidence_id"),
            *entry.get("related_evidence_ids", []),
            *[
                item.get("evidence_id")
                for item in entry.get("evidence_manifest", [])
                if isinstance(item, dict)
            ],
            *[
                item.get("evidence_id")
                for item in entry.get("analysis_manifest", [])
                if isinstance(item, dict)
            ],
        ]
        if evidence_id
    }
    for turn in turns:
        references = set(re.findall(r"\bq_[0-9a-f]{32}\b", turn["answer"]))
        turn["unresolved_evidence_ids"] = sorted(references - known_ids)
    return {
        "format_version": "5",
        "conversation_id": conversation.id,
        "title": conversation.title,
        "engine_name": conversation.engine_name,
        "running": running,
        "limitations": LIMITATIONS,
        "scope_revision": summarize_scope_revision(messages),
        "turns": turns,
    }


def _fence(value: str, language: str = "text") -> str:
    longest = max((len(m.group()) for m in re.finditer(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{value}\n{fence}"


def _cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ")


def _yuan(value: Any) -> str:
    try:
        return f"¥{int(value) / 100:,.2f}"
    except (TypeError, ValueError):
        return "—"


def _one_decimal(value: Any) -> str:
    number = float(value)
    return f"{0 if abs(number) < 0.05 else number:.1f}"


def _render_scope(scope: dict | None, scope_id: str | None) -> list[str]:
    if not scope:
        return []
    baseline = scope.get("baseline", {})
    activity = scope.get("activity", {})
    return [
        "### 本轮口径",
        "",
        "| 活动 | 对比期 | 活动期 | 实验组 | 渠道 | 经营点 | 人群 | 退款口径 |",
        "|---|---|---|---|---|---|---|---|",
        "| "
        + " | ".join(
            _cell(value)
            for value in (
                scope.get("campaign_id"),
                f"{baseline.get('start', '—')} 至 {baseline.get('end', '—')}",
                f"{activity.get('start', '—')} 至 {activity.get('end', '—')}",
                scope.get("variant") or "全部",
                scope.get("channel") or "全部",
                scope.get("location_id") or "全部",
                scope.get("audience") or "全部",
                "退款后" if scope.get("refund_basis") == "after_refunds" else "退款前",
            )
        )
        + " |",
        "",
        f"口径标识：`{_cell(scope_id)}`",
    ]


def _render_task(task: dict | None, task_id: str | None) -> list[str]:
    if not task:
        return []
    decisions = {
        "continue": "是否继续",
        "adjust": "如何调整",
        "scale": "能否扩大",
        "stop": "是否停止",
    }
    risks = {
        "goal": "目标达成",
        "path": "路径损耗",
        "variant": "方案差异",
        "cost": "成本价值",
    }
    context = task.get("business_context") or "未补充"
    return [
        "### 复盘任务",
        "",
        f"- 需支持的决策：{decisions.get(task.get('decision_intent'), _cell(task.get('decision_intent')))}",
        f"- 优先风险：{risks.get(task.get('risk_focus'), _cell(task.get('risk_focus')))}",
        f"- 业务变化或限制：{_cell(context)}",
        f"- 任务标识：`{_cell(task_id)}`",
    ]


def _render_calculation(evidence: list[dict]) -> list[str]:
    result = next((item for item in evidence if item.get("scope_confirmed")), None)
    if not result:
        return []
    lines: list[str] = []
    target = result.get("target", {})
    if target.get("status") == "evaluated":
        names = {
            "conversion_rate": "购买转化率",
            "completed_orders": "完成订单",
            "net_revenue": "净收入",
            "roi": "贡献/活动成本",
        }

        def format_target(value: Any) -> str:
            unit = target.get("unit")
            if unit == "rate":
                return f"{float(value):.1%}"
            if unit == "cents":
                return _yuan(value)
            if unit == "ratio":
                return f"{float(value):.2f}×"
            return f"{float(value):,.0f}"

        lines += [
            "### 目标判断",
            "",
            f"{names.get(target.get('metric'), target.get('metric'))}：实际 **{format_target(target.get('actual_value'))}**，"
            f"目标 {format_target(target.get('target_value'))}，"
            f"**{'已达到' if target.get('achieved') else '未达到'}**。",
            "",
        ]
    lines += [
        "### 确定性计算",
        "",
        "| 周期 | 天数 | 完成订单 | 购买用户 | 净收入 | 商家优惠 | 订单贡献额 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    period_names = {"baseline": "对比期", "activity": "活动期"}
    for row in result.get("rows", []):
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    period_names.get(row.get("period"), row.get("period")),
                    row.get("days"),
                    row.get("completed_orders"),
                    row.get("buyer_users"),
                    _yuan(row.get("net_revenue_cents")),
                    _yuan(row.get("merchant_discount_cents")),
                    _yuan(row.get("contribution_cents")),
                )
            )
            + " |"
        )
    path_diagnostics = result.get("path_diagnostics")
    if isinstance(path_diagnostics, dict) and path_diagnostics.get("rows"):
        lines += [
            "",
            "#### 最低承接节点",
            "",
            "| 实验组 | 相邻阶段 | 承接率 | 未进入下一步 |",
            "|---|---|---:|---:|",
        ]
        for item in path_diagnostics["rows"]:
            lines.append(
                f"| {_cell(item.get('variant'))} | {_cell(item.get('stage_label'))} | "
                f"{float(item.get('continuation_rate') or 0):.1%} | "
                f"{_cell(item.get('not_continued_users'))} |"
            )
        lines += [
            "",
            "按各组相邻阶段最低承接率定位；这是调查优先级，"
            "未进入下一步的人数不等于可恢复增量，也不证明原因。",
        ]
    tail = result.get("attribution_tail")
    if isinstance(tail, dict):
        lines += [
            "",
            "#### 活动结束后的同标签曝光关联订单（单列）",
            "",
            f"配置窗口：{_cell(result.get('attribution_days_configured'))} 天；"
            f"完成订单 {_cell(tail.get('completed_orders'))}、"
            f"购买用户 {_cell(tail.get('buyer_users'))}、"
            f"净收入 {_yuan(tail.get('net_revenue_cents'))}、"
            f"订单贡献额 {_yuan(tail.get('contribution_cents'))}。",
        ]
        if tail.get("candidate_orders") is not None:
            lines += [
                "",
                f"窗口内候选完成订单 {_cell(tail.get('candidate_orders'))} 笔；"
                f"其中未匹配同标签曝光 {_cell(tail.get('orders_without_matching_exposure'))} 笔，"
                f"超出曝光后归因时长 {_cell(tail.get('orders_outside_exposure_window'))} 笔。",
            ]
        lines += [
            "",
            "未提供数据截至时间；不与活动期结果合并判断目标，也不证明活动因果增量。",
        ]
    comparisons = result.get("experiment", {}).get("comparisons", [])
    if comparisons:
        lines += [
            "",
            "#### 实验读数",
            "",
            "| 对比 | 转化率差 | 95% CI | p 值 | 样本检查 |",
            "|---|---:|---:|---:|---|",
        ]
        for item in comparisons:
            low, high = item.get("ci95_difference_pp", [None, None])
            sample = "通过" if item.get("sample_check") == "ok" else "期望频数过小"
            lines.append(
                f"| {_cell(item.get('treatment'))} vs {_cell(item.get('control'))}"
                f" | {float(item.get('difference_pp', 0)):+.1f} 个百分点"
                f" | [{_one_decimal(low)}, {_one_decimal(high)}]"
                f" | {float(item.get('p_value', 1)):.3f} | {sample} |"
            )
        assumption = result.get("experiment", {}).get("assumption")
        if assumption:
            lines += ["", f"> {_cell(assumption)}"]
    evidence_ids = [
        value
        for value in [result.get("evidence_id"), *result.get("related_evidence_ids", [])]
        if value
    ]
    if evidence_ids:
        lines += ["", "证据编号：" + "、".join(f"`{_cell(value)}`" for value in evidence_ids)]
    return lines


def render_markdown(report: dict) -> str:
    lines = [
        "# Campaign Intelligence · 营销活动决策记录",
        "",
        _fence(report["title"]),
        "",
        "本文件直接导出已保存的会话，不代表结论已经人工审核。",
        "",
        *[f"- {item}" for item in report["limitations"]],
    ]
    revision = report.get("scope_revision", {})
    if revision.get("status") == "revised":
        lines += [
            "",
            "## 口径修订审计",
            "",
            "**旧结论已失效。** " + _cell(revision.get("reason")),
            "",
            "| 变更项 | 上一口径 | 当前口径 |",
            "|---|---|---|",
            *[
                f"| {_cell(item.get('label'))} | {_cell(item.get('previous'))} | {_cell(item.get('current'))} |"
                for item in revision.get("changed_fields", [])
            ],
        ]
        if revision.get("previous_decision_superseded"):
            lines += ["", "> 上一口径已落档的决策需要重新确认。"]
    for turn in report["turns"]:
        status_name = {
            "completed": "已完成",
            "completed_with_errors": "完成但有错误",
            "incomplete": "未完成",
            "running": "进行中",
        }.get(turn["status"], turn["status"])
        lines += [
            "",
            f"## 第 {turn['number']} 轮 · {status_name}",
            "",
            "### 问题",
            "",
            _fence(turn["question"]),
            "",
            *_render_task(turn.get("review_task"), turn.get("task_id")),
            "",
            *_render_scope(turn.get("review_scope"), turn.get("scope_id")),
            "",
            *_render_calculation(turn["evidence"]),
            "",
            "### 回答原文",
            "",
            _fence(turn["answer"] or "尚无回答"),
        ]
        if turn.get("decision"):
            decision = turn["decision"]
            outcome_name = {
                "adopted": "按建议执行",
                "modified": "修改后执行",
                "rejected": "暂不采用",
            }.get(decision.get("decision_outcome"), "按建议执行")
            lines += [
                "",
                "### 决策落档",
                "",
                f"- 决定：{outcome_name if decision.get('status') == 'committed' else _cell(decision.get('status'))}",
                f"- 负责人：{_cell(decision.get('owner'))}",
                f"- 复查日期：{_cell(decision.get('review_date'))}",
                f"- 判断依据：{_cell(decision.get('rationale') or '未填写')}",
                f"- 最终动作：{_cell(decision.get('final_action') or '沿用 Agent 建议')}",
                f"- 执行备注：{_cell(decision.get('note') or '无')}",
                f"- Scope ID：`{_cell(decision.get('scope_id'))}`",
                f"- Answer SHA-256：`{_cell(decision.get('answer_sha256'))}`",
            ]
            quality = decision.get("quality_snapshot")
            if quality:
                lines += [
                    f"- 落档时质量门：{int(quality.get('passed', 0))}/{int(quality.get('total', 0))}",
                ]
            experiment_plan = decision.get("experiment_plan")
            if experiment_plan:
                lines += [
                    "",
                    "#### 已绑定实验规划",
                    "",
                    f"- 当前基线：{float(experiment_plan['baseline_rate']):.2%}",
                    f"- 最小可检测提升：{float(experiment_plan['mde_pp']):.1f} pp",
                    f"- 每组样本：{int(experiment_plan['required_per_group']):,} 人",
                    f"- 预计周期：{int(experiment_plan['estimated_days']):,} 天",
                    f"- 实验流量：{float(experiment_plan['traffic_share']):.0%}",
                    f"- 决策规则：{_cell(experiment_plan.get('decision_rule'))}",
                ]
        if turn.get("outcome"):
            outcome = turn["outcome"]
            implementation_name = {
                "completed": "完整执行",
                "partial": "部分执行",
                "not_executed": "未执行",
            }.get(outcome.get("implementation_status"), _cell(outcome.get("implementation_status")))
            next_name = {
                "scale": "扩大执行",
                "iterate": "调整后再验证",
                "stop": "停止方案",
                "collect_more_data": "继续收集数据",
            }.get(outcome.get("next_decision"), _cell(outcome.get("next_decision")))
            lines += [
                "",
                "### 执行结果回流",
                "",
                f"- 执行状态：{implementation_name}",
                f"- 观察日期：{_cell(outcome.get('observed_on'))}",
                f"- 后续决定：{next_name}",
                f"- 数据依据：{_cell(outcome.get('source_reference'))}",
                f"- 业务复盘：{_cell(outcome.get('learning'))}",
            ]
            evaluation = outcome.get("evaluation")
            if evaluation:
                lines += [
                    f"- 系统判定：{_cell(evaluation.get('conclusion'))}",
                    f"- 对照组：{float(evaluation.get('control_rate', 0)):.2%}",
                    f"- 处理组：{float(evaluation.get('treatment_rate', 0)):.2%}",
                    f"- 差异：{float(evaluation.get('difference_pp', 0)):+.2f} 个百分点",
                    f"- 95% 区间：[{float(evaluation.get('ci95_difference_pp', [0, 0])[0]):+.2f}, {float(evaluation.get('ci95_difference_pp', [0, 0])[1]):+.2f}] 个百分点",
                    f"- 因果判读资格：{'满足' if evaluation.get('causal_readout') else '不满足'}",
                ]
        lines += [
            "",
            "### 原始查询依据",
            "",
            _fence(orjson.dumps(turn["evidence"], option=orjson.OPT_INDENT_2).decode(), "json"),
        ]
        if turn["errors"] or turn["unresolved_evidence_ids"]:
            lines += [
                "",
                "### 待核对项",
                "",
                _fence(
                    orjson.dumps(
                        {
                            "errors": turn["errors"],
                            "unresolved_evidence_ids": turn["unresolved_evidence_ids"],
                        },
                        option=orjson.OPT_INDENT_2,
                    ).decode(),
                    "json",
                ),
            ]
    return "\n".join(lines) + "\n"
