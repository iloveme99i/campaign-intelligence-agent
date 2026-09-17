"""Local merchant import endpoint, using existing persisted data-source records."""

import asyncio
import hashlib
import uuid
from datetime import UTC, date, datetime
from typing import Literal

import orjson
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from analytics_agent.db.base import get_session
from analytics_agent.db.models import Message
from analytics_agent.db.repository import ConversationRepo, IntegrationRepo, MessageRepo
from analytics_agent.engines.factory import register_engine
from analytics_agent.merchant.importer import ImportValidationError, import_snapshot
from analytics_agent.merchant.storage import snapshot_directory

router = APIRouter(prefix="/api/merchant", tags=["merchant"])
MAX_UPLOAD_BYTES = 20_000_000
PlanningDimension = Literal["all", "variant", "channel", "location_id", "audience"]
PlanningMetric = Literal[
    "purchase_per_exposure",
    "activation_per_claim",
    "path_purchase_per_activation",
]
DecisionOutcome = Literal["adopted", "modified", "rejected"]
DecisionReason = Literal[
    "evidence_supported",
    "execution_constraint",
    "insufficient_evidence",
    "risk_too_high",
    "priority_changed",
    "other",
]
ImplementationStatus = Literal["completed", "partial", "not_executed"]
MeasurementMethod = Literal["randomized_experiment", "holdout_comparison", "before_after"]
GuardrailStatus = Literal["passed", "failed", "not_measured"]
FollowUpDecision = Literal["scale", "iterate", "stop", "collect_more_data"]


class DecisionCommitRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=80)
    review_date: date
    note: str = Field(default="", max_length=1000)
    decision_outcome: DecisionOutcome = "adopted"
    reason_code: DecisionReason = "evidence_supported"
    rationale: str = Field(default="", max_length=1000)
    final_action: str = Field(default="", max_length=3000)
    expected_scope_id: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    experiment_mde_pp: float | None = Field(default=None, gt=0, lt=100)
    experiment_traffic_share: float | None = Field(default=None, gt=0, le=1)
    experiment_dimension: PlanningDimension = "all"
    experiment_value: str | None = None
    experiment_secondary_dimension: PlanningDimension | None = None
    experiment_secondary_value: str | None = None
    experiment_metric: PlanningMetric = "purchase_per_exposure"

    @model_validator(mode="after")
    def validate_commit(self):
        self.owner = self.owner.strip()
        self.note = self.note.strip()
        self.rationale = self.rationale.strip()
        self.final_action = self.final_action.strip()
        if not self.owner:
            raise ValueError("负责人不能为空")
        if self.review_date < date.today():
            raise ValueError("复查日期不能早于今天")
        if (self.experiment_mde_pp is None) != (self.experiment_traffic_share is None):
            raise ValueError("实验 MDE 与流量比例必须同时提交")
        if self.decision_outcome in {"modified", "rejected"} and not self.rationale:
            raise ValueError("修改或不采用建议时必须填写判断依据")
        if self.decision_outcome == "modified" and not self.final_action:
            raise ValueError("修改后执行时必须填写最终动作")
        if self.decision_outcome == "rejected" and self.experiment_mde_pp is not None:
            raise ValueError("暂不采用本轮建议时不能绑定实验规划")
        if self.decision_outcome == "rejected" and self.reason_code == "evidence_supported":
            raise ValueError("暂不采用时必须选择与驳回原因一致的分类")
        return self


class DecisionOutcomeRequest(BaseModel):
    observed_on: date
    implementation_status: ImplementationStatus
    measurement_method: MeasurementMethod = "before_after"
    randomization_verified: bool = False
    control_total: int | None = Field(default=None, ge=0)
    control_successes: int | None = Field(default=None, ge=0)
    treatment_total: int | None = Field(default=None, ge=0)
    treatment_successes: int | None = Field(default=None, ge=0)
    guardrail_status: GuardrailStatus = "not_measured"
    source_reference: str = Field(default="", max_length=500)
    learning: str = Field(min_length=1, max_length=2000)
    next_decision: FollowUpDecision
    expected_decision_committed_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_outcome(self):
        self.source_reference = self.source_reference.strip()
        self.learning = self.learning.strip()
        if self.observed_on > date.today():
            raise ValueError("观察日期不能晚于今天")
        if not self.learning:
            raise ValueError("必须填写本轮业务复盘")
        counts = (
            self.control_total,
            self.control_successes,
            self.treatment_total,
            self.treatment_successes,
        )
        if self.implementation_status == "not_executed":
            if any(value is not None for value in counts):
                raise ValueError("未执行的方案不能填写实验结果")
            if self.randomization_verified:
                raise ValueError("未执行的方案不能标记为已核验随机分流")
            return self
        if any(value is None for value in counts):
            raise ValueError("已执行或部分执行时必须填写对照组与处理组样本")
        if not self.source_reference:
            raise ValueError("填写结果时必须提供数据依据")
        if self.control_successes > self.control_total:
            raise ValueError("对照组成功人数不能超过合格人数")
        if self.treatment_successes > self.treatment_total:
            raise ValueError("处理组成功人数不能超过合格人数")
        if self.measurement_method != "randomized_experiment" and self.randomization_verified:
            raise ValueError("只有随机实验可以标记为已核验随机分流")
        return self


class ExperimentPlanRequest(BaseModel):
    mde_pp: float = Field(gt=0, lt=100)
    traffic_share: float = Field(default=1.0, gt=0, le=1)
    expected_scope_id: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    planning_dimension: PlanningDimension = "all"
    planning_value: str | None = None
    planning_secondary_dimension: PlanningDimension | None = None
    planning_secondary_value: str | None = None
    planning_metric: PlanningMetric = "purchase_per_exposure"


def _current_review_basis(
    conversation, messages: list[Message], *, running: bool
) -> tuple[dict, dict]:
    """Use one completed, error-free turn; never join evidence from another turn."""
    from analytics_agent.merchant.report import build_report

    turns = build_report(conversation, messages, running=running)["turns"]
    if not turns or turns[-1]["status"] != "completed":
        raise HTTPException(409, "当前轮次尚未形成可确认的结论与证据")
    turn = turns[-1]
    evidence = next(
        (
            item
            for item in reversed(turn["evidence"])
            if item.get("scope_confirmed") is True and item.get("scope_id") == turn["scope_id"]
        ),
        None,
    )
    if not turn["answer"] or not turn["scope_id"] or evidence is None:
        raise HTTPException(409, "当前轮次的结论与已确认口径证据不完整")
    if evidence.get("metric_version") != "v0.8":
        raise HTTPException(409, "历史记录使用旧版指标口径，请重新核算后再测算实验或落档决策")
    required_quality = (
        "buyers_without_matching_variant_exposure",
        "buyers_without_matching_channel_exposure",
        "buyers_without_matching_location_exposure",
        "buyers_without_matching_audience_exposure",
    )
    if any(key not in (evidence.get("data_quality") or {}) for key in required_quality):
        raise HTTPException(409, "当前记录缺少分组身份核验，请重新核算后再测算实验或落档决策")
    return turn, evidence


def _verify_visible_basis(turn: dict, *, scope_id: str, answer: str) -> None:
    if turn["scope_id"] != scope_id or turn["answer"] != answer:
        raise HTTPException(409, "当前结论已更新，请刷新页面后重新核对再确认")


def _build_experiment_plan(
    evidence: dict,
    *,
    mde_pp: float,
    traffic_share: float,
    planning_dimension: PlanningDimension = "all",
    planning_value: str | None = None,
    planning_metric: PlanningMetric = "purchase_per_exposure",
    secondary_dimension: PlanningDimension | None = None,
    secondary_value: str | None = None,
) -> dict:
    from analytics_agent.merchant.experiment import plan_binary_experiment
    from analytics_agent.merchant.readonly import SnapshotReader
    from analytics_agent.merchant.scope import ReviewScope, funnel_query, quality_query
    from analytics_agent.merchant.storage import snapshot_path

    quality = evidence.get("data_quality") or {}
    if int(quality.get("cross_variant_exposed_users") or 0) > 0:
        raise ValueError("当前数据存在跨实验组曝光，先核查分流后再规划下一轮实验")
    if int(quality.get("buyers_without_prior_exposure") or 0) > 0:
        raise ValueError("当前购买用户无法完整链接先前曝光，先修复路径口径后再估算样本")
    activity = evidence["scope"]["activity"]
    activity_days = (
        date.fromisoformat(activity["end"]) - date.fromisoformat(activity["start"])
    ).days + 1
    if bool(secondary_dimension) != bool(secondary_value):
        raise ValueError("secondary dimension and value must be provided together")
    selected_quality = None
    if secondary_dimension is not None:
        if planning_dimension == "all" or secondary_dimension in {"all", planning_dimension}:
            raise ValueError("intersection requires two distinct dimensions")
        if not planning_value or not secondary_value:
            raise ValueError("intersection segments are required")
        for dimension, value in (
            (planning_dimension, planning_value),
            (secondary_dimension, secondary_value),
        ):
            candidates = evidence["segments"][dimension]
            if not candidates or any(
                int(row.get("total_groups", 0)) > len(candidates) for row in candidates
            ):
                raise ValueError("segment scan is incomplete")
            if sum(row.get("dimension_value") == value for row in candidates) != 1:
                raise ValueError("intersection segment is absent or ambiguous")
        scope = ReviewScope.model_validate(evidence["scope"])
        if scope.scope_id != evidence.get("scope_id"):
            raise ValueError("persisted scope does not match analysis evidence")
        for dimension, value in (
            (planning_dimension, planning_value),
            (secondary_dimension, secondary_value),
        ):
            if getattr(scope, dimension) not in (None, value):
                raise ValueError("intersection conflicts with confirmed scope")
        selected_scope = scope.model_copy(
            update={
                planning_dimension: planning_value,
                secondary_dimension: secondary_value,
            }
        )
        sql, parameters = funnel_query(selected_scope)
        reader = SnapshotReader(snapshot_path(evidence["snapshot_id"]))
        result = reader.query(sql, parameters)
        if result.get("error") or result.get("truncated") or not result["rows"]:
            raise ValueError("intersection cannot be verified from snapshot")
        source = result["rows"]
        quality_sql, quality_parameters = quality_query(
            selected_scope,
            matching_dimensions=(planning_dimension, secondary_dimension),
        )
        intersection_quality = reader.query(quality_sql, quality_parameters)
        if (
            intersection_quality.get("error")
            or intersection_quality.get("truncated")
            or len(intersection_quality["rows"]) != 1
        ):
            raise ValueError("intersection path quality cannot be verified")
        selected_quality = intersection_quality["rows"][0]
        if any(
            int(selected_quality.get(key) or 0) > 0
            for key in (
                "cross_variant_exposed_users",
                "buyers_without_prior_exposure",
            )
        ):
            raise ValueError("intersection path has contaminated or unlinked users")
    elif planning_dimension == "all":
        if planning_value:
            raise ValueError("whole-scope planning cannot have a segment")
        source = evidence["funnel"]
    else:
        if not planning_value or not isinstance(planning_value, str):
            raise ValueError("planning segment is required")
        source = evidence["segments"][planning_dimension]
        if not source or any(int(row.get("total_groups", 0)) > len(source) for row in source):
            raise ValueError("segment scan is incomplete")
        source = [row for row in source if row.get("dimension_value") == planning_value]
        if len(source) != 1:
            raise ValueError("planning segment is absent or ambiguous")
    if planning_metric == "purchase_per_exposure" and planning_dimension != "all":
        if selected_quality is None:
            scope = ReviewScope.model_validate(evidence["scope"])
            if scope.scope_id != evidence.get("scope_id"):
                raise ValueError("persisted scope does not match analysis evidence")
            if getattr(scope, planning_dimension) not in (None, planning_value):
                raise ValueError("planning segment conflicts with confirmed scope")
            selected_scope = scope.model_copy(update={planning_dimension: planning_value})
            reader = SnapshotReader(snapshot_path(evidence["snapshot_id"]))
            quality_sql, quality_parameters = quality_query(
                selected_scope, matching_dimensions=(planning_dimension,)
            )
            selected_result = reader.query(quality_sql, quality_parameters)
            if (
                selected_result.get("error")
                or selected_result.get("truncated")
                or len(selected_result["rows"]) != 1
            ):
                raise ValueError("planning path quality cannot be verified")
            selected_quality = selected_result["rows"][0]
        if int(selected_quality.get("buyers_without_matching_selected_exposure") or 0) > 0:
            raise ValueError("selected segment purchases do not match prior exposure labels")
    metric_fields = {
        "purchase_per_exposure": ("exposed_users", "buyer_users"),
        "activation_per_claim": ("claim_users", "activated_users"),
        "path_purchase_per_activation": ("activated_users", "path_buyer_users"),
    }
    denominator, numerator = metric_fields[planning_metric]
    if any(int(row.get(denominator, 0) or 0) < int(row.get(numerator, 0) or 0) for row in source):
        raise ValueError("planning numerator exceeds eligible population")
    rows = [{"exposed_users": row[denominator], "buyer_users": row[numerator]} for row in source]
    plan = plan_binary_experiment(
        rows,
        activity_days=activity_days,
        mde_pp=mde_pp,
        traffic_share=traffic_share,
    )
    plan["population"] = {
        "dimension": planning_dimension,
        "value": planning_value,
        "metric": planning_metric,
        "eligible_users": sum(int(row[denominator]) for row in source),
        "successful_users": sum(int(row[numerator]) for row in source),
        **(
            {
                "secondary_dimension": secondary_dimension,
                "secondary_value": secondary_value,
                "snapshot_id": evidence["snapshot_id"],
                "scope_id": selected_scope.scope_id,
                "source": "recomputed_snapshot_intersection",
            }
            if secondary_dimension
            else {}
        ),
    }
    return plan


async def _register_snapshot(snapshot: dict, session: AsyncSession) -> dict:
    name = "merchant_" + snapshot["snapshot_id"]
    config = {
        "snapshot_id": snapshot["snapshot_id"],
        "synthetic": bool(snapshot.get("synthetic", False)),
    }
    await IntegrationRepo(session).upsert(
        id=str(uuid.uuid4()),
        name=name,
        type="merchant_snapshot",
        label="商家活动快照",
        config=orjson.dumps(config).decode(),
        source="ui",
    )
    register_engine(name, "merchant_snapshot", config)
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "engine_name": name,
        "row_counts": snapshot["row_counts"],
        "metric_version": snapshot["metric_version"],
    }


@router.get("/snapshots/{engine_name}/catalog")
async def snapshot_catalog(engine_name: str, session: AsyncSession = Depends(get_session)):
    from analytics_agent.merchant.catalog import describe_snapshot
    from analytics_agent.merchant.readonly import SnapshotReader
    from analytics_agent.merchant.storage import snapshot_path

    record = await IntegrationRepo(session).get(engine_name)
    if record is None or record.type != "merchant_snapshot":
        raise HTTPException(404, "没有找到活动数据")
    try:
        config = orjson.loads(record.config)
        reader = SnapshotReader(snapshot_path(config["snapshot_id"]), max_rows=5000)
        catalog = await asyncio.to_thread(describe_snapshot, reader)
        catalog["synthetic"] = bool(config.get("synthetic", False))
        return catalog
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(409, "无法读取活动范围，请检查数据或重新导入") from exc


@router.get("/examples.zip")
async def download_examples(templates_only: bool = False):
    from analytics_agent.merchant.examples import example_archive

    content = await asyncio.to_thread(example_archive, templates_only=templates_only)
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="merchant-templates.zip"'
            if templates_only
            else 'attachment; filename="merchant-synthetic-example.zip"',
        },
    )


@router.post("/examples", status_code=201)
async def create_example_snapshot(session: AsyncSession = Depends(get_session)):
    from analytics_agent.merchant.examples import example_exports

    snapshot = await asyncio.to_thread(import_snapshot, example_exports(), snapshot_directory())
    snapshot["synthetic"] = True
    return await _register_snapshot(snapshot, session)


@router.get("/conversations/{conversation_id}/trace-quality")
async def trace_quality(conversation_id: str, session: AsyncSession = Depends(get_session)):
    from analytics_agent.merchant.evaluation import summarize_trace_quality

    conversation = await ConversationRepo(session).get(conversation_id)
    if conversation is None or not conversation.engine_name.startswith("merchant_"):
        raise HTTPException(404, "没有找到活动决策记录")
    messages = await MessageRepo(session).list_for_conversation(conversation_id)
    records = [
        {
            "event_type": message.event_type,
            "role": message.role,
            "payload": orjson.loads(message.payload),
            "created_at": message.created_at,
        }
        for message in messages
    ]
    return summarize_trace_quality(records)


@router.get("/conversations/{conversation_id}/scope-revision")
async def scope_revision(conversation_id: str, session: AsyncSession = Depends(get_session)):
    from analytics_agent.merchant.revision import summarize_scope_revision

    conversation = await ConversationRepo(session).get(conversation_id)
    if conversation is None or not conversation.engine_name.startswith("merchant_"):
        raise HTTPException(404, "没有找到活动决策记录")
    messages = await MessageRepo(session).list_for_conversation(conversation_id)
    return summarize_scope_revision(messages)


@router.post("/conversations/{conversation_id}/experiment-plan")
async def experiment_plan(
    conversation_id: str,
    body: ExperimentPlanRequest,
    session: AsyncSession = Depends(get_session),
):
    from analytics_agent.api.chat import _active_streams

    conversation = await ConversationRepo(session).get(conversation_id)
    if conversation is None or not conversation.engine_name.startswith("merchant_"):
        raise HTTPException(404, "没有找到活动决策记录")
    messages = await MessageRepo(session).list_for_conversation(conversation_id)
    turn, evidence = _current_review_basis(
        conversation, messages, running=conversation_id in _active_streams
    )
    _verify_visible_basis(turn, scope_id=body.expected_scope_id, answer=body.expected_answer)
    if not isinstance(evidence.get("funnel"), list):
        raise HTTPException(409, "当前记录缺少可用于实验测算的曝光与转化证据")
    try:
        return _build_experiment_plan(
            evidence,
            mde_pp=body.mde_pp,
            traffic_share=body.traffic_share,
            planning_dimension=body.planning_dimension,
            planning_value=body.planning_value,
            planning_metric=body.planning_metric,
            secondary_dimension=body.planning_secondary_dimension,
            secondary_value=body.planning_secondary_value,
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        detail = (
            "所选分组的订单标签与先前曝光不一致；购买/曝光无法构成同一人群，请核对数据后重新核算。"
            if str(exc) == "selected segment purchases do not match prior exposure labels"
            else "当前证据无法形成可靠的实验样本测算"
        )
        raise HTTPException(409, detail) from exc


@router.get("/conversations/{conversation_id}/report")
async def export_report(
    conversation_id: str,
    format: Literal["json", "md"] = "json",
    session: AsyncSession = Depends(get_session),
):
    from analytics_agent.api.chat import _active_streams
    from analytics_agent.merchant.report import build_report, render_markdown

    conversation = await ConversationRepo(session).get(conversation_id)
    if conversation is None or not conversation.engine_name.startswith("merchant_"):
        raise HTTPException(404, "没有找到活动复盘记录")
    messages = await MessageRepo(session).list_for_conversation(conversation_id)
    report = build_report(conversation, messages, running=conversation_id in _active_streams)
    content = (
        orjson.dumps(report, option=orjson.OPT_INDENT_2)
        if format == "json"
        else render_markdown(report).encode()
    )
    return Response(
        content=content,
        media_type="application/json" if format == "json" else "text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="campaign-review.{format}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/conversations/{conversation_id}/decisions", status_code=201)
async def commit_decision(
    conversation_id: str,
    body: DecisionCommitRequest,
    session: AsyncSession = Depends(get_session),
):
    from analytics_agent.api.chat import _active_streams

    conversation = await ConversationRepo(session).get(conversation_id)
    if conversation is None or not conversation.engine_name.startswith("merchant_"):
        raise HTTPException(404, "没有找到活动决策记录")
    messages = await MessageRepo(session).list_for_conversation(conversation_id)
    turn, evidence = _current_review_basis(
        conversation, messages, running=conversation_id in _active_streams
    )
    _verify_visible_basis(turn, scope_id=body.expected_scope_id, answer=body.expected_answer)
    if turn["decision"] is not None:
        raise HTTPException(409, "本轮决策已落档，请刷新页面查看")
    quality_snapshot = None
    if evidence.get("analysis_manifest"):
        from analytics_agent.merchant.evaluation import score_trace

        records = [
            {
                "event_type": message.event_type,
                "role": message.role,
                "payload": orjson.loads(message.payload),
                "created_at": message.created_at,
            }
            for message in messages
        ]
        quality = score_trace(records)
        quality_snapshot = {
            "passed": sum(quality.checks.values()),
            "total": len(quality.checks),
            "failed_checks": [key for key, passed in quality.checks.items() if not passed],
        }
        if not quality.passed and body.decision_outcome != "rejected":
            raise HTTPException(409, "本轮分析尚未通过证据与决策边界审计，不能直接落档")
    experiment_plan = None
    if body.experiment_mde_pp is not None and body.experiment_traffic_share is not None:
        if not isinstance(evidence.get("funnel"), list):
            raise HTTPException(409, "当前记录缺少可用于实验测算的曝光与转化证据")
        try:
            experiment_plan = _build_experiment_plan(
                evidence,
                mde_pp=body.experiment_mde_pp,
                traffic_share=body.experiment_traffic_share,
                planning_dimension=body.experiment_dimension,
                planning_value=body.experiment_value,
                planning_metric=body.experiment_metric,
                secondary_dimension=body.experiment_secondary_dimension,
                secondary_value=body.experiment_secondary_value,
            )
        except (KeyError, TypeError, ValueError, OSError) as exc:
            detail = (
                "所选分组的订单标签与先前曝光不一致；购买/曝光无法构成同一人群，请核对数据后重新核算。"
                if str(exc) == "selected segment purchases do not match prior exposure labels"
                else "当前证据无法形成可靠的实验样本测算"
            )
            raise HTTPException(409, detail) from exc
    payload = {
        "status": "committed",
        "decision_outcome": body.decision_outcome,
        "reason_code": body.reason_code,
        "rationale": body.rationale,
        "final_action": body.final_action,
        "owner": body.owner,
        "review_date": body.review_date.isoformat(),
        "note": body.note,
        "scope_id": turn["scope_id"],
        "answer_sha256": hashlib.sha256(turn["answer"].encode()).hexdigest(),
        "committed_at": datetime.now(UTC).isoformat(),
        "experiment_plan": experiment_plan,
        "quality_snapshot": quality_snapshot,
    }
    message = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        event_type="DECISION",
        role="user",
        payload=orjson.dumps(payload).decode(),
        sequence=await MessageRepo(session).next_sequence(conversation_id),
        created_at=datetime.now(UTC),
    )
    await MessageRepo(session).create(message)
    await ConversationRepo(session).touch(conversation_id)
    return payload


@router.post("/conversations/{conversation_id}/outcomes", status_code=201)
async def record_decision_outcome(
    conversation_id: str,
    body: DecisionOutcomeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Bind an observed execution result to the latest immutable decision."""
    from analytics_agent.api.chat import _active_streams

    conversation = await ConversationRepo(session).get(conversation_id)
    if conversation is None or not conversation.engine_name.startswith("merchant_"):
        raise HTTPException(404, "没有找到活动决策记录")
    messages = await MessageRepo(session).list_for_conversation(conversation_id)
    turn, _ = _current_review_basis(
        conversation, messages, running=conversation_id in _active_streams
    )
    decision = turn.get("decision")
    if decision is None:
        raise HTTPException(409, "先完成本轮决策落档，再记录执行结果")
    if decision.get("decision_outcome") == "rejected":
        raise HTTPException(409, "本轮建议已标记为不采用，不应登记为已执行方案")
    if turn.get("outcome") is not None:
        raise HTTPException(409, "本轮执行结果已回流，请刷新页面查看")
    if decision.get("committed_at") != body.expected_decision_committed_at:
        raise HTTPException(409, "决策记录已变化，请刷新后重新核对")

    evaluation = None
    if body.implementation_status != "not_executed":
        from analytics_agent.merchant.experiment import assess_binary_outcome

        plan = decision.get("experiment_plan")
        if body.measurement_method == "randomized_experiment" and not plan:
            raise HTTPException(409, "本轮未预先绑定实验规划，不能将事后分组登记为随机实验")
        try:
            evaluation = assess_binary_outcome(
                control_total=int(body.control_total),
                control_successes=int(body.control_successes),
                treatment_total=int(body.treatment_total),
                treatment_successes=int(body.treatment_successes),
                measurement_method=body.measurement_method,
                randomization_verified=body.randomization_verified,
                guardrail_status=body.guardrail_status,
                required_total=(int(plan["required_total"]) if plan else None),
                mde_pp=(float(plan["mde_pp"]) if plan else None),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(422, "当前样本无法形成可靠的结果判读") from exc

    payload = {
        "status": "recorded",
        "outcome_id": str(uuid.uuid4()),
        "decision_committed_at": decision["committed_at"],
        "scope_id": decision["scope_id"],
        "observed_on": body.observed_on.isoformat(),
        "implementation_status": body.implementation_status,
        "measurement_method": body.measurement_method,
        "randomization_verified": body.randomization_verified,
        "guardrail_status": body.guardrail_status,
        "source_reference": body.source_reference,
        "learning": body.learning,
        "next_decision": body.next_decision,
        "counts": (
            {
                "control_total": body.control_total,
                "control_successes": body.control_successes,
                "treatment_total": body.treatment_total,
                "treatment_successes": body.treatment_successes,
            }
            if body.implementation_status != "not_executed"
            else None
        ),
        "evaluation": evaluation,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    message = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        event_type="OUTCOME",
        role="user",
        payload=orjson.dumps(payload).decode(),
        sequence=await MessageRepo(session).next_sequence(conversation_id),
        created_at=datetime.now(UTC),
    )
    await MessageRepo(session).create(message)
    await ConversationRepo(session).touch(conversation_id)
    return payload


@router.post("/snapshots", status_code=201)
async def upload_snapshot(
    campaigns: UploadFile,
    events: UploadFile,
    orders: UploadFile,
    costs: UploadFile,
    incrementality: UploadFile | None = None,
    session: AsyncSession = Depends(get_session),
):
    files = {
        "campaigns": campaigns,
        "events": events,
        "orders": orders,
        "costs": costs,
        **({"incrementality": incrementality} if incrementality is not None else {}),
    }
    content: dict[str, bytes] = {}
    remaining = MAX_UPLOAD_BYTES
    try:
        for name, upload in files.items():
            data = await upload.read(remaining + 1)
            if len(data) > remaining:
                raise HTTPException(413, "导入文件合计不能超过 20 MB")
            remaining -= len(data)
            content[name] = data
        try:
            snapshot = await asyncio.to_thread(import_snapshot, content, snapshot_directory())
        except ImportValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Never expose the filesystem path to the client.
        return await _register_snapshot(snapshot, session)
    finally:
        for upload in files.values():
            await upload.close()
