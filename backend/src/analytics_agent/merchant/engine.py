"""Request-scoped merchant tools compatible with the upstream Agent graph."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Literal

import orjson
from langchain_core.tools import BaseTool, tool

from analytics_agent.engines.base import QueryEngine
from analytics_agent.merchant.experiment import (
    assess_incrementality_readiness,
    assess_variants,
)
from analytics_agent.merchant.importer import FIELDS
from analytics_agent.merchant.incrementality import (
    IncrementalityError,
    estimate_difference_in_differences,
)
from analytics_agent.merchant.journey import analyze_path_bottlenecks
from analytics_agent.merchant.readonly import SnapshotReader
from analytics_agent.merchant.scope import (
    ReviewScope,
    ReviewTask,
    attribution_tail_query,
    breakdown_query,
    comparison_query,
    cost_query,
    coverage_query,
    funnel_query,
    quality_query,
    timeline_query,
)
from analytics_agent.merchant.target import evaluate_target


def _evidence_manifest_entry(kind: str, result: dict) -> dict:
    """Persist enough metadata to replay a component query without duplicating rows."""
    return {
        key: value
        for key, value in {
            "kind": kind,
            "evidence_id": result.get("evidence_id"),
            "snapshot_id": result.get("snapshot_id"),
            "scope_id": result.get("scope_id"),
            "metric_version": result.get("metric_version"),
            "provenance": result.get("provenance"),
            "sql": result.get("sql"),
            "parameters": result.get("parameters"),
            "row_count": len(result.get("rows", [])),
            "truncated": result.get("truncated"),
            "elapsed_ms": result.get("elapsed_ms"),
            "warnings": result.get("warnings", []),
        }.items()
        if value is not None
    }


def _cost_boundary(scope: ReviewScope, rows: list[dict]) -> dict:
    """Describe whether costs can be assigned to all selected dimensions."""
    total = sum(int(row.get("activity_cost_cents") or 0) for row in rows)
    if scope.location_id is not None or scope.audience is not None:
        return {
            "status": "shared_costs_unallocated",
            "attributable_cost_cents": 0,
            "shared_cost_cents": total,
            "detail": (
                "成本文件没有经营点或人群维度，不能将全活动费用归属于当前筛选范围；"
                "补充分摊规则前，不计算该范围的投入效率。"
            ),
        }
    shared = sum(
        int(row.get("activity_cost_cents") or 0)
        for row in rows
        if (scope.channel is not None and row.get("channel") == "all")
        or (scope.variant is not None and row.get("variant") == "all")
    )
    if scope.channel is None and scope.variant is None:
        return {
            "status": "fully_scoped",
            "attributable_cost_cents": total,
            "shared_cost_cents": 0,
            "detail": "成本与当前活动范围一致。",
        }
    attributable = total - shared
    if shared:
        return {
            "status": "shared_costs_unallocated",
            "requested_channel": scope.channel,
            "attributable_cost_cents": attributable,
            "shared_cost_cents": shared,
            "detail": (
                "成本文件包含全活动共享费用，不能将其全部归属于当前渠道或实验组；"
                "在补充分摊规则前，不计算当前筛选范围的投入效率。"
            ),
        }
    return {
        "status": "fully_scoped",
        "requested_channel": scope.channel,
        "attributable_cost_cents": attributable,
        "shared_cost_cents": 0,
        "detail": "成本可直接归属于当前筛选范围。",
    }


PATH_METRIC_BY_STAGE = {
    "claim_to_activation": "activation_per_claim",
    "activation_to_path_purchase": "path_purchase_per_activation",
}


def _yuan(cents: int | float) -> str:
    return f"{float(cents) / 100:,.2f} 元"


def _decision_contract(result: dict, task: ReviewTask | None) -> dict:
    """Freeze the business facts that prose generation is not allowed to alter."""
    boundary = result.get("cost_boundary") or {}
    if boundary.get("status") == "shared_costs_unallocated":
        cost_statement = (
            f"当前范围可归属成本为 {_yuan(boundary.get('attributable_cost_cents') or 0)}；"
            f"全活动共享费用 {_yuan(boundary.get('shared_cost_cents') or 0)} 尚未分摊，"
            "当前无法判断经营结果能否覆盖活动投入。"
        )
    else:
        cost_statement = (
            f"当前范围可归属成本为 {_yuan(boundary.get('attributable_cost_cents') or 0)}。"
        )

    path = result.get("path_diagnostics") or {}
    path_rows = [
        {
            key: row.get(key)
            for key in (
                "variant",
                "stage_key",
                "stage_label",
                "from_users",
                "to_users",
                "continuation_rate",
                "not_continued_users",
            )
        }
        for row in path.get("rows", [])
        if isinstance(row, dict)
    ]
    stages = {str(row.get("stage_key")) for row in path_rows if row.get("stage_key")}
    common_stage = next(iter(stages)) if len(stages) == 1 else None
    primary_metric = (
        PATH_METRIC_BY_STAGE.get(common_stage, "purchase_per_exposure")
        if task and task.risk_focus == "path"
        else "purchase_per_exposure"
    )
    return {
        "version": 1,
        "immutable": True,
        "cost_statement": cost_statement,
        "path": {
            "evidence_id": path.get("evidence_id"),
            "common_stage": common_stage,
            "rows": path_rows,
        },
        "next_experiment": {
            "required_primary_metric": primary_metric,
            "supported_primary_metrics": [
                "purchase_per_exposure",
                "activation_per_claim",
                "path_purchase_per_activation",
            ],
        },
    }


def _experiment_contract(experiment: dict | None) -> dict | None:
    if not isinstance(experiment, dict):
        return None
    return {
        "metric": experiment.get("metric", "purchase_per_exposure"),
        "status": experiment.get("status"),
        "comparisons": [
            {
                "control": item.get("control"),
                "treatment": item.get("treatment"),
                "control_rate_pct": round(float(item.get("control_rate") or 0) * 100, 2),
                "treatment_rate_pct": round(float(item.get("treatment_rate") or 0) * 100, 2),
                "difference_pp": round(float(item.get("difference_pp") or 0), 2),
                "ci95_difference_pp": [
                    round(float(bound), 2) for bound in item.get("ci95_difference_pp", [])
                ],
                "p_value": round(float(item.get("p_value") or 0), 4),
            }
            for item in experiment.get("comparisons", [])
            if isinstance(item, dict)
        ],
    }


def build_analysis_brief(result: dict, task: ReviewTask | None) -> dict:
    """Give the model a decision brief, not every precomputed diagnostic row.

    The UI and trace retain the complete deterministic result. The model receives
    a bounded multi-lens scan and may request focused diagnostic rechecks.
    """
    funnel_rows = [row for row in result.get("funnel", []) if isinstance(row, dict)]
    funnel_totals = {
        field: sum((row.get(field) or 0) for row in funnel_rows)
        for field in (
            "exposed_users",
            "landing_users",
            "claim_users",
            "activated_users",
            "buyer_users",
            "path_buyer_users",
            "activated_without_path_purchase_users",
            "completed_orders",
            "net_revenue_cents",
            "contribution_cents",
        )
    }
    cost_rows = [row for row in result.get("costs", []) if isinstance(row, dict)]
    analysis_manifest = {
        item.get("kind"): item.get("evidence_id")
        for item in result.get("analysis_manifest", [])
        if isinstance(item, dict)
    }
    segment_scan = {}
    for dimension, rows in result.get("segments", {}).items():
        if not isinstance(rows, list):
            continue
        ranked = sorted(
            (row for row in rows if isinstance(row, dict)),
            key=lambda row: row.get("exposed_users") or 0,
            reverse=True,
        )
        segment_scan[dimension] = {
            "total_groups": ranked[0].get("total_groups", len(ranked)) if ranked else 0,
            "evidence_id": (
                next(
                    (
                        entry.get("evidence_id")
                        for entry in result.get("evidence_manifest", [])
                        if entry.get("kind") == "funnel"
                    ),
                    None,
                )
                if dimension == "variant"
                else analysis_manifest.get(f"segment:{dimension}")
            ),
            "leading_groups": [
                {
                    key: row.get(key)
                    for key in (
                        "dimension_value",
                        "exposed_users",
                        "claim_users",
                        "activated_users",
                        "buyer_users",
                        "path_buyer_users",
                        "activated_without_path_purchase_users",
                        "activation_to_path_purchase_rate",
                        "completed_orders",
                        "order_contribution_cents",
                        "conversion_rate",
                    )
                }
                for row in ranked[:6]
            ],
        }
    return {
        "analysis_stage": "headline_complete_diagnosis_required" if task else "headline_complete",
        "task": task.model_dump(mode="json") if task else None,
        "task_id": task.task_id if task else None,
        "scope_id": result.get("scope_id"),
        "snapshot_id": result.get("snapshot_id"),
        "metric_version": result.get("metric_version"),
        "rows": result.get("rows", []),
        "segment_scan": segment_scan,
        "timeline": {
            "bin_days": result.get("timeline_bin_days"),
            "evidence_id": analysis_manifest.get("timeline"),
            "rows": result.get("timeline", []),
            "total_bins": len(result.get("timeline", [])),
        },
        "data_quality": result.get("data_quality"),
        "data_quality_evidence_id": analysis_manifest.get("data_quality"),
        "data_coverage": result.get("data_coverage"),
        "data_coverage_evidence_id": analysis_manifest.get("data_coverage"),
        "attribution_tail": result.get("attribution_tail"),
        "attribution_days_configured": result.get("attribution_days_configured"),
        "attribution_tail_evidence_id": analysis_manifest.get("attribution_tail"),
        "path_diagnostics": result.get("path_diagnostics"),
        "funnel_totals": funnel_totals,
        "activity_cost_cents": (
            None
            if result.get("cost_boundary", {}).get("status") == "shared_costs_unallocated"
            else sum((row.get("activity_cost_cents") or 0) for row in cost_rows)
        ),
        "cost_boundary": result.get("cost_boundary"),
        "decision_contract": _decision_contract(result, task),
        "incrementality_readiness": result.get("incrementality_readiness"),
        "incrementality": result.get("incrementality"),
        "target": result.get("target"),
        "warnings": result.get("warnings", []),
        "evidence_manifest": result.get("evidence_manifest", []),
        "related_evidence_ids": result.get("related_evidence_ids", []),
        "available_diagnostics": {
            "variant": "判断实验组差异、效率与成本权衡",
            "channel": "定位触达渠道的路径与价值差异",
            "location_id": "定位经营点执行差异",
            "audience": "定位目标人群响应差异",
        },
        "instruction": (
            "先比较时间走势与实验组、渠道、经营点、人群四个全景扫描，再结合任务与限制定位主要矛盾；"
            "对需要解释的差异调用 diagnose_dimension 做定向复核，填写简短业务理由。"
            if task
            else "已有多维扫描证据；有明确矛盾或缺口时再定向复核。"
        ),
    }


class MerchantQueryEngine(QueryEngine):
    name = "merchant_snapshot"

    def __init__(self, snapshot_path: Path, *, max_queries: int = 30, synthetic: bool = False):
        if not 1 <= max_queries <= 30:
            raise ValueError("max_queries must be between 1 and 30")
        self.reader = SnapshotReader(snapshot_path)
        self.snapshot_id = snapshot_path.stem
        self.synthetic = synthetic
        self.max_queries = max_queries
        self._lock = threading.Lock()
        self._queries = 0
        self._evidence: list[dict] = []
        self.confirmed_scope: ReviewScope | None = None

    @property
    def evidence(self) -> list[dict]:
        """Return a copy for persistence with the turn, not mutable internal state."""
        with self._lock:
            return orjson.loads(orjson.dumps(self._evidence))

    def _query(
        self, sql: str, parameters: dict | None = None, scope: ReviewScope | None = None
    ) -> str:
        with self._lock:
            if self._queries >= self.max_queries:
                return orjson.dumps({"error": "query_budget_exhausted", "rows": []}).decode()
            self._queries += 1
        result = self.reader.query(sql, parameters)
        evidence_id = "q_" + uuid.uuid4().hex
        result.update(
            evidence_id=evidence_id,
            snapshot_id=self.snapshot_id,
            metric_version="v0.8",
            provenance="synthetic" if self.synthetic else "imported",
        )
        if scope is not None:
            result.update(
                scope_id=scope.scope_id,
                scope=scope.model_dump(mode="json"),
                warnings=scope.warnings,
                sql=sql,
                parameters=parameters,
            )
            if any(row.get("completed_orders") == 0 for row in result.get("rows", [])):
                result["warnings"].append("至少一个期间没有匹配订单：不能据此判断无经营或增长。")
        with self._lock:
            self._evidence.append({"sql": sql, "parameters": parameters or {}, **result})
        return orjson.dumps(result).decode()

    def _incrementality(self, scope: ReviewScope) -> dict:
        """Evaluate an optional panel without persisting thousands of raw rows."""
        if any((scope.variant, scope.channel, scope.location_id, scope.audience)):
            return {
                "status": "not_estimated",
                "detail": (
                    "同期对照面板当前只声明到活动整体；筛选方案、渠道、经营点或人群后，"
                    "不得沿用全活动增量估计。"
                ),
                "estimates": [],
                "_readiness_inputs": {},
            }
        sql = """SELECT panel_unit, period, metric, treated, post, outcome,
          interference_reviewed FROM incrementality
          WHERE campaign_id=:campaign_id ORDER BY metric, panel_unit, period"""
        parameters = {"campaign_id": scope.campaign_id}
        result = SnapshotReader(self.reader.path, max_rows=5000, timeout_seconds=5).query(
            sql, parameters
        )
        evidence_id = "q_" + uuid.uuid4().hex
        base = {
            "evidence_id": evidence_id,
            "snapshot_id": self.snapshot_id,
            "scope_id": scope.scope_id,
            "metric_version": "v0.8",
            "provenance": "synthetic" if self.synthetic else "imported",
        }
        if result.get("error") == "query_rejected":
            return {
                "status": "not_supplied",
                "detail": "当前快照未包含同期对照面板，本轮只报告观察性前后变化。",
                "estimates": [],
                "_readiness_inputs": {},
            }
        if result.get("error") or result.get("truncated"):
            compact = {
                **base,
                "status": "not_estimated",
                "detail": "增量面板超出 5,000 行审计上限或读取失败，本轮不输出因果估计。",
                "estimates": [],
                "_readiness_inputs": {},
            }
        elif not result.get("rows"):
            return {
                "status": "not_supplied",
                "detail": "未导入同期对照面板，本轮只报告观察性前后变化。",
                "estimates": [],
                "_readiness_inputs": {},
            }
        else:
            panel_matches_scope = all(
                (
                    scope.activity.start.isoformat()
                    <= str(row["period"])
                    <= scope.activity.end.isoformat()
                )
                if bool(row["post"])
                else str(row["period"]) < scope.activity.start.isoformat()
                for row in result["rows"]
            )
            if not panel_matches_scope:
                return {
                    "status": "not_estimated",
                    "detail": (
                        "同期对照面板的前后周期与当前活动期不一致；"
                        "需按当前日期重新导入面板，不能复用旧估计。"
                    ),
                    "estimates": [],
                    "_readiness_inputs": {},
                }
            grouped: dict[str, list[dict]] = {}
            for row in result["rows"]:
                grouped.setdefault(str(row["metric"]), []).append(row)
            estimates = []
            for metric, rows in grouped.items():
                try:
                    estimate = estimate_difference_in_differences(rows, metric=metric)
                    if not all(bool(row["interference_reviewed"]) for row in rows):
                        estimate.update(
                            status="validation_failed",
                            decision_ready=False,
                            causal_boundary=(
                                "干扰与外溢风险尚未审查，不得将该估计解读为活动因果增量。"
                            ),
                        )
                    estimates.append(estimate)
                except IncrementalityError as exc:
                    estimates.append(
                        {
                            "status": "invalid_panel",
                            "metric": metric,
                            "decision_ready": False,
                            "detail": str(exc),
                            "causal_boundary": "面板不满足 DiD 识别条件，不输出因果增量。",
                        }
                    )
            compact = {
                **base,
                "status": (
                    "identified"
                    if estimates and all(item.get("decision_ready") for item in estimates)
                    else "validation_failed"
                ),
                "detail": "按指标独立执行平衡面板 DiD、前趋势与安慰剂检验。",
                "estimates": estimates,
                "_readiness_inputs": {
                    "has_concurrent_control": all(
                        {bool(row.get("treated")) for row in rows} == {False, True}
                        for rows in grouped.values()
                    ),
                    "repeated_pre_periods": min(
                        len({row.get("period") for row in rows if not row.get("post")})
                        for rows in grouped.values()
                    ),
                    "panel_unit_consistent": all(
                        item.get("status") != "invalid_panel" for item in estimates
                    ),
                    "outcome_complete": True,
                    "interference_reviewed": all(
                        bool(row.get("interference_reviewed")) for row in result["rows"]
                    ),
                },
            }
        with self._lock:
            public_compact = {
                key: value for key, value in compact.items() if not key.startswith("_")
            }
            self._evidence.append(
                {
                    "sql": sql,
                    "parameters": parameters,
                    **public_compact,
                    "rows": compact.get("estimates", []),
                    "truncated": bool(result.get("truncated")),
                    "elapsed_ms": result.get("elapsed_ms"),
                }
            )
        return compact

    def comparison_tool(self) -> BaseTool:
        engine = self

        @tool
        def compare_periods(scope: ReviewScope) -> str:
            """Recalculate outcome, journey funnel and cost for a user-confirmed campaign scope.

            Use after campaign, dates and attribution dimensions are known, and again after correction.
            Returns actual aggregate rows and immutable evidence/scope IDs, never a cached answer.
            No matching orders is missing evidence, not proof of zero business.
            """
            if engine.confirmed_scope is not None and scope != engine.confirmed_scope:
                return orjson.dumps(
                    {
                        "error": "scope_requires_user_confirmation",
                        "confirmed_scope_id": engine.confirmed_scope.scope_id,
                    }
                ).decode()
            sql, parameters = comparison_query(scope)
            outcome = orjson.loads(engine._query(sql, parameters, scope))
            funnel_sql, funnel_parameters = funnel_query(scope)
            funnel = orjson.loads(engine._query(funnel_sql, funnel_parameters, scope))
            cost_sql, cost_parameters = cost_query(scope)
            costs = orjson.loads(engine._query(cost_sql, cost_parameters, scope))
            timeline_sql, timeline_parameters = timeline_query(scope)
            timeline = orjson.loads(engine._query(timeline_sql, timeline_parameters, scope))
            quality_sql, quality_parameters = quality_query(scope)
            quality = orjson.loads(engine._query(quality_sql, quality_parameters, scope))
            coverage_sql, coverage_parameters = coverage_query(scope)
            coverage = orjson.loads(engine._query(coverage_sql, coverage_parameters, scope))
            segment_results = {}
            for dimension in ("channel", "location_id", "audience"):
                segment_sql, segment_parameters = breakdown_query(scope, dimension)
                segment_results[dimension] = orjson.loads(
                    engine._query(segment_sql, segment_parameters, scope)
                )
            cost_boundary = _cost_boundary(scope, costs.get("rows", []))
            target = orjson.loads(
                engine._query(
                    "SELECT primary_metric, target_value, attribution_days, end_date FROM campaigns WHERE campaign_id=:campaign_id",
                    {"campaign_id": scope.campaign_id},
                    scope,
                )
            )
            components = [
                outcome,
                funnel,
                costs,
                timeline,
                quality,
                coverage,
                target,
                *segment_results.values(),
            ]
            if any("error" in item or item.get("truncated") for item in components):
                return orjson.dumps(
                    {
                        "error": "analysis_incomplete",
                        "detail": "本轮核算有查询失败或结果被截断，不能形成完整复盘。",
                        "scope_id": scope.scope_id,
                    }
                ).decode()
            if not target.get("rows"):
                return orjson.dumps(
                    {
                        "error": "campaign_not_found",
                        "scope_id": scope.scope_id,
                    }
                ).decode()
            campaign = target.get("rows", [{}])[0] if target.get("rows") else {}
            incrementality = engine._incrementality(scope)
            readiness_inputs = incrementality.pop("_readiness_inputs", {})
            path_diagnostics = {
                **analyze_path_bottlenecks(funnel.get("rows", [])),
                "evidence_id": funnel.get("evidence_id"),
            }
            attribution_days = int(campaign.get("attribution_days") or 0)
            tail = None
            tail_status = (
                "computed"
                if campaign.get("end_date") == scope.activity.end.isoformat()
                else "requires_campaign_end"
            )
            if tail_status == "computed":
                tail_sql, tail_parameters = attribution_tail_query(scope, attribution_days)
                tail = orjson.loads(engine._query(tail_sql, tail_parameters, scope))
                if "error" in tail or tail.get("truncated"):
                    return orjson.dumps(
                        {
                            "error": "analysis_incomplete",
                            "detail": "活动结束后的归因窗口核算失败，不能形成完整复盘。",
                            "scope_id": scope.scope_id,
                        }
                    ).decode()
            return orjson.dumps(
                {
                    **outcome,
                    "funnel": funnel.get("rows", []),
                    "path_diagnostics": path_diagnostics,
                    "timeline": timeline.get("rows", []),
                    "timeline_bin_days": timeline_parameters["bin_days"],
                    "data_quality": quality.get("rows", [{}])[0],
                    "data_coverage": coverage.get("rows", [{}])[0],
                    "attribution_tail": tail.get("rows", [{}])[0] if tail else None,
                    "attribution_tail_status": tail_status,
                    "segments": {
                        "variant": [
                            {
                                **row,
                                "dimension_value": row["variant"],
                                "order_contribution_cents": row["contribution_cents"],
                            }
                            for row in funnel.get("rows", [])
                        ],
                        **{
                            dimension: result.get("rows", [])
                            for dimension, result in segment_results.items()
                        },
                    },
                    "analysis_manifest": [
                        _evidence_manifest_entry("timeline", timeline),
                        _evidence_manifest_entry("data_quality", quality),
                        _evidence_manifest_entry("data_coverage", coverage),
                        *([_evidence_manifest_entry("attribution_tail", tail)] if tail else []),
                        *(
                            _evidence_manifest_entry(f"segment:{dimension}", result)
                            for dimension, result in segment_results.items()
                        ),
                    ],
                    "costs": costs.get("rows", []),
                    "cost_boundary": cost_boundary,
                    "experiment": assess_variants(
                        funnel.get("rows", []),
                        cross_variant_exposed_users=(
                            quality.get("rows", [{}])[0].get("cross_variant_exposed_users", 0)
                        ),
                        buyers_without_prior_exposure=(
                            quality.get("rows", [{}])[0].get("buyers_without_prior_exposure", 0)
                        ),
                        buyers_without_matching_variant_exposure=(
                            quality.get("rows", [{}])[0].get(
                                "buyers_without_matching_variant_exposure", 0
                            )
                        ),
                    ),
                    "incrementality_readiness": assess_incrementality_readiness(
                        has_pre_assignment_eligibility=False,
                        has_assignment_log=False,
                        has_concurrent_control=bool(readiness_inputs.get("has_concurrent_control")),
                        randomization_unit=None,
                        repeated_pre_periods=int(readiness_inputs.get("repeated_pre_periods", 0)),
                        panel_unit_consistent=bool(readiness_inputs.get("panel_unit_consistent")),
                        outcome_complete=bool(readiness_inputs.get("outcome_complete")),
                        interference_reviewed=bool(readiness_inputs.get("interference_reviewed")),
                    ),
                    "incrementality": incrementality,
                    "target": evaluate_target(
                        campaign,
                        outcome.get("rows", []),
                        funnel.get("rows", []),
                        costs.get("rows", []),
                        cost_boundary=cost_boundary,
                        quality=quality.get("rows", [{}])[0],
                    ),
                    "attribution_days_configured": attribution_days,
                    "related_evidence_ids": [
                        item
                        for item in (
                            funnel.get("evidence_id"),
                            costs.get("evidence_id"),
                            target.get("evidence_id"),
                            timeline.get("evidence_id"),
                            quality.get("evidence_id"),
                            coverage.get("evidence_id"),
                            tail.get("evidence_id") if tail else None,
                            *(item.get("evidence_id") for item in segment_results.values()),
                            incrementality.get("evidence_id"),
                        )
                        if item
                    ],
                    "evidence_manifest": [
                        _evidence_manifest_entry("outcome", outcome),
                        _evidence_manifest_entry("funnel", funnel),
                        _evidence_manifest_entry("cost", costs),
                        _evidence_manifest_entry("target", target),
                        *(
                            [
                                {
                                    "kind": "incrementality",
                                    **{
                                        key: incrementality[key]
                                        for key in (
                                            "evidence_id",
                                            "snapshot_id",
                                            "scope_id",
                                            "metric_version",
                                            "provenance",
                                        )
                                        if key in incrementality
                                    },
                                }
                            ]
                            if incrementality.get("evidence_id")
                            else []
                        ),
                    ],
                }
            ).decode()

        return compare_periods

    def get_tools(self) -> list[BaseTool]:
        engine = self
        known_tables = {*FIELDS, "incrementality", "order_metrics", "campaign_funnel"}

        @tool
        def execute_sql(sql: str) -> str:
            """Query the merchant campaign snapshot read-only.

            Returns real rows, evidence_id, truncation and errors. Quote evidence_id
            when using these numbers. campaign_funnel is an unscoped exploration
            view; use confirmed-scope tools for decision evidence. contribution_cents
            is not net profit.
            """
            return engine._query(sql)

        @tool
        def list_tables() -> str:
            """List imported tables and deterministic metric views."""
            return orjson.dumps([{"name": name} for name in sorted(known_tables)]).decode()

        @tool
        def get_schema(table: str) -> str:
            """Read column names. Only tables from list_tables are allowed."""
            if table not in known_tables:
                return orjson.dumps({"error": "unknown_table"}).decode()
            return engine._query(f'SELECT * FROM "{table}" LIMIT 0')

        @tool
        def preview_table(table: str, limit: int = 5) -> str:
            """Preview a few rows; a preview is not a complete aggregate."""
            if table not in known_tables or not 1 <= limit <= 20:
                return orjson.dumps({"error": "invalid_preview"}).decode()
            return engine._query(f'SELECT * FROM "{table}" LIMIT {limit}')

        @tool
        def diagnose_dimension(
            dimension: Literal["variant", "channel", "location_id", "audience"],
            reason: str,
        ) -> str:
            """Recheck a decision-relevant dimension after the full multi-lens scan.

            Choose a dimension needed to explain a specific observed gap.
            Returns journey nodes, conversion and order contribution with evidence.
            It does not prove causality. The scope is bound by the server and
            cannot be supplied or modified by the model.
            """
            reason = reason.strip()
            if not reason or len(reason) > 280:
                return orjson.dumps({"error": "diagnostic_reason_required"}).decode()
            scope = engine.confirmed_scope
            if scope is None:
                return orjson.dumps(
                    {
                        "error": "scope_requires_user_confirmation",
                    }
                ).decode()
            sql, parameters = breakdown_query(scope, dimension)
            breakdown = orjson.loads(engine._query(sql, parameters, scope))
            if "error" in breakdown or breakdown.get("truncated"):
                return orjson.dumps(breakdown).decode()
            diagnostic_quality = None
            if dimension == "variant":
                quality_sql, quality_parameters = quality_query(scope)
                diagnostic_quality = orjson.loads(
                    engine._query(quality_sql, quality_parameters, scope)
                )
                if (
                    "error" in diagnostic_quality
                    or diagnostic_quality.get("truncated")
                    or not diagnostic_quality.get("rows")
                ):
                    return orjson.dumps({"error": "diagnostic_quality_incomplete"}).decode()
            experiment = (
                {
                    "metric": "purchase_per_exposure",
                    **assess_variants(
                        [
                            {
                                **row,
                                "variant": row.get("dimension_value"),
                                "contribution_cents": row.get("order_contribution_cents"),
                            }
                            for row in breakdown.get("rows", [])
                        ],
                        cross_variant_exposed_users=(
                            diagnostic_quality["rows"][0]["cross_variant_exposed_users"]
                            if diagnostic_quality
                            else 0
                        ),
                        buyers_without_prior_exposure=(
                            diagnostic_quality["rows"][0]["buyers_without_prior_exposure"]
                            if diagnostic_quality
                            else 0
                        ),
                        buyers_without_matching_variant_exposure=(
                            diagnostic_quality["rows"][0][
                                "buyers_without_matching_variant_exposure"
                            ]
                            if diagnostic_quality
                            else 0
                        ),
                    ),
                }
                if dimension == "variant"
                else None
            )
            response = {
                **breakdown,
                "diagnostic_dimension": dimension,
                "diagnostic_reason": reason,
                "experiment": experiment,
                "decision_contract": {
                    "version": 1,
                    "immutable": True,
                    "experiment": _experiment_contract(experiment),
                },
                "evidence_manifest": [
                    _evidence_manifest_entry(f"diagnosis:{dimension}", breakdown)
                ],
                "related_evidence_ids": [],
            }
            if diagnostic_quality:
                response["data_quality"] = diagnostic_quality["rows"][0]
                response["related_evidence_ids"].append(diagnostic_quality["evidence_id"])
                response["evidence_manifest"].append(
                    _evidence_manifest_entry("diagnosis:data_quality", diagnostic_quality)
                )
            if dimension in {"variant", "channel"}:
                # Costs only carry variant/channel dimensions in the import contract.
                cost_sql = f"""SELECT {dimension} AS dimension_value,
                  COALESCE(SUM(amount_cents),0) AS activity_cost_cents
                  FROM costs WHERE campaign_id=:campaign_id
                  AND business_date BETWEEN :activity_start AND :activity_end
                  AND (:variant IS NULL OR variant=:variant OR variant='all')
                  AND (:channel IS NULL OR channel=:channel OR channel='all')
                  GROUP BY {dimension} ORDER BY activity_cost_cents DESC"""
                costs = orjson.loads(
                    engine._query(
                        cost_sql,
                        {
                            "campaign_id": scope.campaign_id,
                            "variant": scope.variant,
                            "channel": scope.channel,
                            "activity_start": scope.activity.start.isoformat(),
                            "activity_end": scope.activity.end.isoformat(),
                        },
                        scope,
                    )
                )
                if "error" in costs or costs.get("truncated"):
                    return orjson.dumps({"error": "diagnostic_cost_incomplete"}).decode()
                response["costs"] = costs.get("rows", [])
                response["cost_boundary"] = _cost_boundary(scope, costs.get("rows", []))
                if costs.get("evidence_id"):
                    response["related_evidence_ids"].append(costs["evidence_id"])
                response["evidence_manifest"].append(
                    _evidence_manifest_entry(f"diagnosis:{dimension}:cost", costs)
                )
            else:
                response["cost_boundary"] = (
                    "成本导出不含经营点或人群字段，不能按该维度拆分活动投入。"
                )
            return orjson.dumps(response).decode()

        return [execute_sql, list_tables, get_schema, preview_table, diagnose_dimension]

    async def aclose(self) -> None:
        # SnapshotReader opens and closes a connection for each query.
        pass
