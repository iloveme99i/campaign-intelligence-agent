import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analytics_agent.api.conversations import router as conversations_router
from analytics_agent.api.merchant import router
from analytics_agent.db.base import get_session
from analytics_agent.db.models import Base
from analytics_agent.db.repository import IntegrationRepo
from analytics_agent.engines.resolver import resolve_engine
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_importer import exports


class UploadAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = create_async_engine("sqlite+aiosqlite:///" + str(self.root / "app.db"))
        self.addAsyncCleanup(self.db.dispose)
        async with self.db.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.db, expire_on_commit=False)

        async def session_override():
            async with self.factory() as session:
                yield session

        self.app = FastAPI()
        self.app.include_router(router)
        self.app.include_router(conversations_router)
        self.app.dependency_overrides[get_session] = session_override
        for location in (
            "analytics_agent.api.merchant.snapshot_directory",
            "analytics_agent.merchant.storage.snapshot_directory",
        ):
            p = patch(location, return_value=self.root / "snapshots")
            p.start()
            self.addCleanup(p.stop)
        registry = patch("analytics_agent.engines.factory._registry", {})
        registry.start()
        self.addCleanup(registry.stop)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_upload_persists_connection_and_creates_restorable_conversation(self):
        response = await self.client.post(
            "/api/merchant/snapshots",
            files={k: (k + ".csv", v, "text/csv") for k, v in exports().items()},
        )
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertNotIn("path", data)
        async with self.factory() as session:
            record = await IntegrationRepo(session).get(data["engine_name"])
            self.assertEqual(record.type, "merchant_snapshot")
            first = await resolve_engine(data["engine_name"], session)
            second = await resolve_engine(data["engine_name"], session)
            self.assertIsNot(first, second)
            self.assertEqual(
                first.reader.query("SELECT COUNT(*) AS n FROM order_metrics")["rows"], [{"n": 2}]
            )
        created = await self.client.post(
            "/api/conversations", json={"title": "满减活动复盘", "engine_name": data["engine_name"]}
        )
        self.assertEqual(created.status_code, 201, created.text)
        restored = await self.client.get("/api/conversations/" + created.json()["id"])
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["engine_name"], data["engine_name"])

    async def test_upload_accepts_optional_incrementality_panel(self):
        data = exports()
        data["incrementality"] = (
            b"panel_unit,campaign_id,period,metric,treated,post,outcome,interference_reviewed\n"
            b"store_1,c,2026-07-01,completed_orders,0,0,10,1\n"
            b"store_1,c,2026-08-01,completed_orders,0,1,12,1\n"
        )

        response = await self.client.post(
            "/api/merchant/snapshots",
            files={key: (key + ".csv", value, "text/csv") for key, value in data.items()},
        )

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["row_counts"]["incrementality"], 2)

    async def test_rejected_data_does_not_register_connection(self):
        data = exports()
        data["costs"] = data["costs"].replace(b",500\n", b",-500\n")
        response = await self.client.post(
            "/api/merchant/snapshots",
            files={k: (k + ".csv", v, "text/csv") for k, v in data.items()},
        )
        self.assertEqual(response.status_code, 422)
        async with self.factory() as session:
            self.assertEqual(await IntegrationRepo(session).list_all(), [])

    async def test_example_catalog_is_explicitly_marked_synthetic(self):
        response = await self.client.post("/api/merchant/examples")
        self.assertEqual(response.status_code, 201, response.text)
        engine_name = response.json()["engine_name"]
        catalog = await self.client.get(f"/api/merchant/snapshots/{engine_name}/catalog")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        body = catalog.json()
        self.assertTrue(body["synthetic"])
        self.assertEqual(body["campaign_count"], 3)
        self.assertEqual(
            {item["campaign_name"] for item in body["campaigns"]},
            {
                "字节系 · 全域大促",
                "腾讯游戏 · 回流任务季",
                "腾讯零售 · 私域联动",
            },
        )

    async def test_confirmed_scope_is_computed_and_persisted_before_model_answer(self):
        import orjson
        from analytics_agent.api.chat import ConvStream, _run_and_broadcast
        from analytics_agent.db.repository import MessageRepo
        from analytics_agent.merchant.runtime import build_merchant_graph
        from analytics_agent.merchant.scope import ReviewScope, ReviewTask
        from langchain_core.messages import AIMessage
        from test_runtime import ScriptedToolModel
        from test_scope import scope_data

        response = await self.client.post(
            "/api/merchant/snapshots",
            files={k: (k + ".csv", v, "text/csv") for k, v in exports().items()},
        )
        engine_name = response.json()["engine_name"]
        created = await self.client.post("/api/conversations", json={"engine_name": engine_name})
        cid = created.json()["id"]
        model = ScriptedToolModel(
            messages=iter([AIMessage(content="测试回答，不代表真实分析效果")])
        )

        def graph_for_test(engine):
            return build_merchant_graph(engine, model=model)

        with (
            patch("analytics_agent.db.base._get_session_factory", return_value=self.factory),
            patch(
                "analytics_agent.merchant.runtime.build_merchant_graph", side_effect=graph_for_test
            ),
            patch(
                "analytics_agent.agent.analysis.compute_context_quality",
                side_effect=RuntimeError("disabled in test"),
            ),
        ):
            await _run_and_broadcast(
                cid,
                ConvStream(task=None),
                "按此口径复盘",
                engine_name,
                15,
                ReviewScope.model_validate(scope_data()),
                "explicit",
                ReviewTask(
                    decision_intent="adjust",
                    risk_focus="cost",
                    business_context="部分门店缺货",
                ),
            )
        async with self.factory() as session:
            messages = await MessageRepo(session).list_for_conversation(cid)
        persisted = [(m.event_type, orjson.loads(m.payload)) for m in messages]
        self.assertIn("review_scope", persisted[0][1])
        self.assertEqual(persisted[0][1]["review_task"]["decision_intent"], "adjust")
        self.assertEqual(len(persisted[0][1]["task_id"]), 64)
        sql_index = next(i for i, (kind, _) in enumerate(persisted) if kind == "SQL")
        complete_index = next(i for i, (kind, _) in enumerate(persisted) if kind == "COMPLETE")
        self.assertLess(sql_index, complete_index)
        self.assertTrue(persisted[sql_index][1]["scope_confirmed"])
        self.assertEqual(
            persisted[sql_index][1]["scope_id"], ReviewScope.model_validate(scope_data()).scope_id
        )

    async def test_followup_inherits_scope_and_explicit_change_replaces_it(self):
        from analytics_agent.api import chat
        from analytics_agent.merchant.scope import ReviewScope
        from test_scope import scope_data

        self.app.include_router(chat.router)
        created = await self.client.post(
            "/api/conversations", json={"engine_name": "merchant_test"}
        )
        cid = created.json()["id"]
        async with self.factory() as session:
            await chat._persist_message(
                session, cid, "TEXT", "user", {"text": "确认", "review_scope": scope_data()}, 0
            )
            await session.commit()
        captured = []

        async def worker(*args):
            captured.append((args[5], args[6]))
            stream = args[1]
            stream.done = True
            for queue in stream.subs:
                queue.put_nowait(None)

        with (
            patch.object(chat, "_active_streams", {}),
            patch.object(chat, "_run_and_broadcast", side_effect=worker),
        ):
            response = await self.client.post(
                f"/api/conversations/{cid}/messages", json={"text": "继续拆解"}
            )
            self.assertEqual(response.status_code, 200)
            changed = scope_data() | {"refund_basis": "before_refunds"}
            response = await self.client.post(
                f"/api/conversations/{cid}/messages", json={"text": "修改", "review_scope": changed}
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual(
            captured,
            [
                (ReviewScope.model_validate(scope_data()), "inherited"),
                (ReviewScope.model_validate(changed), "explicit"),
            ],
        )

    async def test_followup_inherits_structured_review_task(self):
        from analytics_agent.api import chat
        from analytics_agent.merchant.scope import ReviewTask

        self.app.include_router(chat.router)
        created = await self.client.post(
            "/api/conversations", json={"engine_name": "merchant_test"}
        )
        cid = created.json()["id"]
        task = {
            "decision_intent": "scale",
            "risk_focus": "cost",
            "business_context": "部分门店缺货",
        }
        async with self.factory() as session:
            await chat._persist_message(
                session,
                cid,
                "TEXT",
                "user",
                {"text": "确认任务", "review_task": task},
                0,
            )
            await session.commit()
        captured = []

        async def worker(*args):
            captured.append((args[7], args[8]))
            stream = args[1]
            stream.done = True
            for queue in stream.subs:
                queue.put_nowait(None)

        with (
            patch.object(chat, "_active_streams", {}),
            patch.object(chat, "_run_and_broadcast", side_effect=worker),
        ):
            response = await self.client.post(
                f"/api/conversations/{cid}/messages", json={"text": "继续拆解"}
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(
            captured,
            [(ReviewTask.model_validate(task), "inherited")],
        )

    async def test_parallel_send_is_rejected_without_launching_worker(self):
        from analytics_agent.api import chat

        self.app.include_router(chat.router)
        created = await self.client.post(
            "/api/conversations", json={"engine_name": "merchant_test"}
        )
        cid = created.json()["id"]
        with (
            patch.object(chat, "_active_streams", {cid: chat.ConvStream(task=None)}),
            patch.object(chat, "_run_and_broadcast") as worker,
        ):
            response = await self.client.post(
                f"/api/conversations/{cid}/messages", json={"text": "重复发送"}
            )
        self.assertEqual(response.status_code, 409)
        worker.assert_not_called()

    async def test_experiment_plan_uses_persisted_evidence_instead_of_client_metrics(self):
        import uuid
        from datetime import UTC, datetime

        import orjson
        from analytics_agent.db.models import Message
        from analytics_agent.db.repository import MessageRepo

        created = await self.client.post(
            "/api/conversations",
            json={"title": "活动决策", "engine_name": "merchant_test"},
        )
        cid = created.json()["id"]
        payload = {
            "scope_confirmed": True,
            "metric_version": "v0.8",
            "scope_id": "scope-plan",
            "data_quality": {
                "buyers_without_matching_variant_exposure": 0,
                "buyers_without_matching_channel_exposure": 0,
                "buyers_without_matching_location_exposure": 0,
                "buyers_without_matching_audience_exposure": 0,
            },
            "scope": {"activity": {"start": "2026-08-01", "end": "2026-08-10"}},
            "funnel": [
                {"variant": "control", "exposed_users": 500, "buyer_users": 25},
                {"variant": "treatment", "exposed_users": 500, "buyer_users": 25},
            ],
        }
        async with self.factory() as session:
            for sequence, (event_type, role, item) in enumerate(
                [
                    ("TEXT", "user", {"text": "复盘", "scope_id": "scope-plan"}),
                    ("SQL", "assistant", payload),
                    ("COMPLETE", "assistant", {"text": "下一轮实验建议"}),
                ]
            ):
                await MessageRepo(session).create(
                    Message(
                        id=str(uuid.uuid4()),
                        conversation_id=cid,
                        event_type=event_type,
                        role=role,
                        payload=orjson.dumps(item).decode(),
                        sequence=sequence,
                        created_at=datetime.now(UTC),
                    )
                )

        response = await self.client.post(
            f"/api/merchant/conversations/{cid}/experiment-plan",
            json={
                "mde_pp": 1.0,
                "traffic_share": 0.5,
                "expected_scope_id": "scope-plan",
                "expected_answer": "下一轮实验建议",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertAlmostEqual(response.json()["baseline_rate"], 0.05)
        self.assertEqual(response.json()["traffic_share"], 0.5)
        self.assertGreater(response.json()["required_per_group"], 0)

    async def test_scope_revision_endpoint_reads_persisted_turns(self):
        import uuid
        from datetime import UTC, datetime

        import orjson
        from analytics_agent.db.models import Message
        from analytics_agent.db.repository import MessageRepo

        created = await self.client.post(
            "/api/conversations",
            json={"title": "活动决策", "engine_name": "merchant_test"},
        )
        cid = created.json()["id"]
        base_scope = {
            "campaign_id": "summer",
            "baseline": {"start": "2026-08-01", "end": "2026-08-07"},
            "activity": {"start": "2026-08-08", "end": "2026-08-14"},
            "variant": None,
            "channel": None,
            "location_id": None,
            "audience": None,
            "refund_basis": "after_refunds",
        }
        events = [
            ("TEXT", "user", {"text": "全渠道", "scope_id": "one", "review_scope": base_scope}),
            (
                "SQL",
                "assistant",
                {
                    "scope_confirmed": True,
                    "rows": [{"period": "activity", "completed_orders": 100}],
                },
            ),
            ("COMPLETE", "assistant", {"text": "旧结论"}),
            (
                "TEXT",
                "user",
                {
                    "text": "只看直播",
                    "scope_id": "two",
                    "review_scope": {**base_scope, "channel": "直播"},
                },
            ),
            (
                "SQL",
                "assistant",
                {"scope_confirmed": True, "rows": [{"period": "activity", "completed_orders": 80}]},
            ),
            ("COMPLETE", "assistant", {"text": "新结论"}),
        ]
        async with self.factory() as session:
            repo = MessageRepo(session)
            for sequence, (event_type, role, payload) in enumerate(events):
                await repo.create(
                    Message(
                        id=str(uuid.uuid4()),
                        conversation_id=cid,
                        event_type=event_type,
                        role=role,
                        payload=orjson.dumps(payload).decode(),
                        sequence=sequence,
                        created_at=datetime.now(UTC),
                    )
                )

        response = await self.client.get(f"/api/merchant/conversations/{cid}/scope-revision")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "revised")
        self.assertEqual(response.json()["changed_fields"][0]["label"], "渠道")

    async def test_decision_commit_binds_owner_to_scope_and_answer(self):
        import uuid
        from datetime import UTC, date, datetime, timedelta

        import orjson
        from analytics_agent.db.models import Message
        from analytics_agent.db.repository import MessageRepo

        created = await self.client.post(
            "/api/conversations",
            json={"title": "活动决策", "engine_name": "merchant_test"},
        )
        cid = created.json()["id"]
        async with self.factory() as session:
            repo = MessageRepo(session)
            for sequence, (event_type, role, payload) in enumerate(
                [
                    ("TEXT", "user", {"text": "复盘", "scope_id": "scope-123"}),
                    (
                        "SQL",
                        "assistant",
                        {
                            "scope_confirmed": True,
                            "metric_version": "v0.8",
                            "scope_id": "scope-123",
                            "data_quality": {
                                "buyers_without_matching_variant_exposure": 0,
                                "buyers_without_matching_channel_exposure": 0,
                                "buyers_without_matching_location_exposure": 0,
                                "buyers_without_matching_audience_exposure": 0,
                            },
                            "scope": {
                                "activity": {
                                    "start": "2026-08-01",
                                    "end": "2026-08-10",
                                }
                            },
                            "funnel": [
                                {
                                    "variant": "control",
                                    "exposed_users": 500,
                                    "buyer_users": 25,
                                },
                                {
                                    "variant": "treatment",
                                    "exposed_users": 500,
                                    "buyer_users": 25,
                                },
                            ],
                        },
                    ),
                    ("COMPLETE", "assistant", {"text": "结论与下一轮行动"}),
                ]
            ):
                await repo.create(
                    Message(
                        id=str(uuid.uuid4()),
                        conversation_id=cid,
                        event_type=event_type,
                        role=role,
                        payload=orjson.dumps(payload).decode(),
                        sequence=sequence,
                        created_at=datetime.now(UTC),
                    )
                )

        response = await self.client.post(
            f"/api/merchant/conversations/{cid}/decisions",
            json={
                "owner": " 增长运营 ",
                "review_date": (date.today() + timedelta(days=7)).isoformat(),
                "note": "下轮结束后复查",
                "decision_outcome": "modified",
                "reason_code": "execution_constraint",
                "rationale": "现有触达资源不足，需要缩小到阶段任务人群。",
                "final_action": "仅在阶段任务人群内随机验证新增付费引导。",
                "expected_scope_id": "scope-123",
                "expected_answer": "结论与下一轮行动",
                "experiment_mde_pp": 1.0,
                "experiment_traffic_share": 0.5,
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["owner"], "增长运营")
        self.assertEqual(body["decision_outcome"], "modified")
        self.assertEqual(body["reason_code"], "execution_constraint")
        self.assertIn("阶段任务", body["final_action"])
        self.assertEqual(body["scope_id"], "scope-123")
        self.assertEqual(len(body["answer_sha256"]), 64)
        self.assertEqual(body["experiment_plan"]["traffic_share"], 0.5)
        self.assertGreater(body["experiment_plan"]["required_per_group"], 0)
        detail = await self.client.get(f"/api/conversations/{cid}")
        decision = next(
            item for item in detail.json()["messages"] if item["event_type"] == "DECISION"
        )
        self.assertEqual(decision["payload"]["answer_sha256"], body["answer_sha256"])
        self.assertEqual(
            decision["payload"]["experiment_plan"]["required_total"],
            body["experiment_plan"]["required_total"],
        )

        per_group = body["experiment_plan"]["required_per_group"]
        outcome_response = await self.client.post(
            f"/api/merchant/conversations/{cid}/outcomes",
            json={
                "observed_on": date.today().isoformat(),
                "implementation_status": "completed",
                "measurement_method": "randomized_experiment",
                "randomization_verified": True,
                "control_total": per_group,
                "control_successes": round(per_group * 0.05),
                "treatment_total": per_group,
                "treatment_successes": round(per_group * 0.07),
                "guardrail_status": "passed",
                "source_reference": "实验平台 EXP-2026-0915",
                "learning": "处理组达到预设业务提升，贡献额护栏未触发。",
                "next_decision": "scale",
                "expected_decision_committed_at": body["committed_at"],
            },
        )
        self.assertEqual(outcome_response.status_code, 201, outcome_response.text)
        outcome = outcome_response.json()
        self.assertEqual(outcome["scope_id"], "scope-123")
        self.assertEqual(outcome["evaluation"]["status"], "decision_threshold_met")
        self.assertTrue(outcome["evaluation"]["causal_readout"])
        duplicate = await self.client.post(
            f"/api/merchant/conversations/{cid}/outcomes",
            json={
                "observed_on": date.today().isoformat(),
                "implementation_status": "not_executed",
                "learning": "重复提交",
                "next_decision": "stop",
                "expected_decision_committed_at": body["committed_at"],
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        detail = await self.client.get(f"/api/conversations/{cid}")
        persisted_outcome = next(
            item for item in detail.json()["messages"] if item["event_type"] == "OUTCOME"
        )
        self.assertEqual(persisted_outcome["payload"]["outcome_id"], outcome["outcome_id"])

    async def test_decision_feedback_validates_modified_and_rejected_outcomes(self):
        from datetime import date, timedelta

        from analytics_agent.api.merchant import DecisionCommitRequest
        from pydantic import ValidationError

        common = {
            "owner": "运营负责人",
            "review_date": date.today() + timedelta(days=7),
            "expected_scope_id": "scope-1",
            "expected_answer": "结论",
        }
        with self.assertRaises(ValidationError):
            DecisionCommitRequest(**common, decision_outcome="modified")
        with self.assertRaises(ValidationError):
            DecisionCommitRequest(
                **common,
                decision_outcome="rejected",
                rationale="证据不足",
                experiment_mde_pp=1,
                experiment_traffic_share=1,
            )
        with self.assertRaises(ValidationError):
            DecisionCommitRequest(
                **common,
                decision_outcome="rejected",
                rationale="暂不采用",
                reason_code="evidence_supported",
            )
        rejected = DecisionCommitRequest(
            **common,
            decision_outcome="rejected",
            reason_code="insufficient_evidence",
            rationale="  缺少完整尾窗数据  ",
        )
        self.assertEqual(rejected.rationale, "缺少完整尾窗数据")

    async def test_decision_outcome_requires_samples_for_executed_work(self):
        from datetime import date

        from analytics_agent.api.merchant import DecisionOutcomeRequest
        from pydantic import ValidationError

        common = {
            "observed_on": date.today(),
            "learning": "复查执行结果",
            "next_decision": "iterate",
            "expected_decision_committed_at": "2026-09-15T00:00:00Z",
        }
        with self.assertRaises(ValidationError):
            DecisionOutcomeRequest(
                **common,
                implementation_status="completed",
                source_reference="实验平台",
            )
        not_executed = DecisionOutcomeRequest(
            **common,
            implementation_status="not_executed",
        )
        self.assertEqual(not_executed.measurement_method, "before_after")

    async def test_failed_agent_answer_can_be_rejected_but_not_adopted(self):
        import uuid
        from datetime import UTC, date, datetime, timedelta

        import orjson
        from analytics_agent.db.models import Message
        from analytics_agent.db.repository import MessageRepo

        created = await self.client.post(
            "/api/conversations",
            json={"title": "失败回答反馈", "engine_name": "merchant_test"},
        )
        cid = created.json()["id"]
        evidence_id = "q_12345678"
        events = [
            (
                "TEXT",
                "user",
                {
                    "text": "复盘",
                    "scope_id": "scope-failed",
                    "review_scope": {
                        "campaign_id": "failed-case",
                        "baseline": {"start": "2026-07-25", "end": "2026-07-31"},
                        "activity": {"start": "2026-08-01", "end": "2026-08-07"},
                        "variant": None,
                        "channel": None,
                        "location_id": None,
                        "audience": None,
                        "refund_basis": "after_refunds",
                    },
                },
            ),
            (
                "SQL",
                "assistant",
                {
                    "scope_confirmed": True,
                    "metric_version": "v0.8",
                    "scope_id": "scope-failed",
                    "evidence_id": evidence_id,
                    "related_evidence_ids": [evidence_id],
                    "analysis_manifest": [{"kind": "timeline", "evidence_id": evidence_id}],
                    "data_quality": {
                        "buyers_without_matching_variant_exposure": 0,
                        "buyers_without_matching_channel_exposure": 0,
                        "buyers_without_matching_location_exposure": 0,
                        "buyers_without_matching_audience_exposure": 0,
                    },
                    "scope": {"activity": {"start": "2026-08-01", "end": "2026-08-07"}},
                    "funnel": [],
                },
            ),
            ("COMPLETE", "assistant", {"text": "没有证据的错误答案"}),
        ]
        async with self.factory() as session:
            repo = MessageRepo(session)
            for sequence, (event_type, role, payload) in enumerate(events):
                await repo.create(
                    Message(
                        id=str(uuid.uuid4()),
                        conversation_id=cid,
                        event_type=event_type,
                        role=role,
                        payload=orjson.dumps(payload).decode(),
                        sequence=sequence,
                        created_at=datetime.now(UTC),
                    )
                )

        common = {
            "owner": "运营负责人",
            "review_date": (date.today() + timedelta(days=7)).isoformat(),
            "rationale": "回答未通过质量门，不能进入执行。",
            "expected_scope_id": "scope-failed",
            "expected_answer": "没有证据的错误答案",
        }
        endpoint = f"/api/merchant/conversations/{cid}/decisions"
        adopted = await self.client.post(
            endpoint,
            json={**common, "decision_outcome": "adopted"},
        )
        self.assertEqual(adopted.status_code, 409, adopted.text)
        rejected = await self.client.post(
            endpoint,
            json={
                **common,
                "decision_outcome": "rejected",
                "reason_code": "insufficient_evidence",
            },
        )
        self.assertEqual(rejected.status_code, 201, rejected.text)
        body = rejected.json()
        self.assertEqual(body["decision_outcome"], "rejected")
        self.assertLess(
            body["quality_snapshot"]["passed"],
            body["quality_snapshot"]["total"],
        )

    async def test_decision_rejects_stale_incomplete_failed_and_duplicate_turns(self):
        import uuid
        from datetime import UTC, date, datetime, timedelta

        import orjson
        from analytics_agent.db.models import Message
        from analytics_agent.db.repository import MessageRepo

        created = await self.client.post(
            "/api/conversations",
            json={"title": "多轮决策", "engine_name": "merchant_test"},
        )
        cid = created.json()["id"]

        async def append(event_type, role, payload):
            async with self.factory() as session:
                repo = MessageRepo(session)
                await repo.create(
                    Message(
                        id=str(uuid.uuid4()),
                        conversation_id=cid,
                        event_type=event_type,
                        role=role,
                        payload=orjson.dumps(payload).decode(),
                        sequence=await repo.next_sequence(cid),
                        created_at=datetime.now(UTC),
                    )
                )

        def request(scope_id, answer):
            return {
                "owner": "运营负责人",
                "review_date": (date.today() + timedelta(days=7)).isoformat(),
                "expected_scope_id": scope_id,
                "expected_answer": answer,
            }

        endpoint = f"/api/merchant/conversations/{cid}/decisions"
        await append("TEXT", "user", {"text": "全渠道复盘", "scope_id": "scope-a"})
        current_quality = {
            "buyers_without_matching_variant_exposure": 0,
            "buyers_without_matching_channel_exposure": 0,
            "buyers_without_matching_location_exposure": 0,
            "buyers_without_matching_audience_exposure": 0,
        }
        await append(
            "SQL",
            "assistant",
            {
                "scope_confirmed": True,
                "metric_version": "v0.8",
                "data_quality": current_quality,
                "scope_id": "scope-a",
            },
        )
        await append("COMPLETE", "assistant", {"text": "旧结论"})
        first = await self.client.post(endpoint, json=request("scope-a", "旧结论"))
        self.assertEqual(first.status_code, 201, first.text)
        duplicate = await self.client.post(endpoint, json=request("scope-a", "旧结论"))
        self.assertEqual(duplicate.status_code, 409)

        await append("TEXT", "user", {"text": "只看某渠道", "scope_id": "scope-b"})
        await append(
            "SQL",
            "assistant",
            {
                "scope_confirmed": True,
                "metric_version": "v0.8",
                "data_quality": current_quality,
                "scope_id": "scope-b",
            },
        )
        pending = await self.client.post(endpoint, json=request("scope-a", "旧结论"))
        self.assertEqual(pending.status_code, 409)
        await append("COMPLETE", "assistant", {"text": "新结论"})
        stale = await self.client.post(endpoint, json=request("scope-a", "旧结论"))
        self.assertEqual(stale.status_code, 409)
        stale_plan = await self.client.post(
            f"/api/merchant/conversations/{cid}/experiment-plan",
            json={
                "mde_pp": 1.0,
                "traffic_share": 1.0,
                "expected_scope_id": "scope-a",
                "expected_answer": "旧结论",
            },
        )
        self.assertEqual(stale_plan.status_code, 409)
        second = await self.client.post(endpoint, json=request("scope-b", "新结论"))
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["scope_id"], "scope-b")

        await append("TEXT", "user", {"text": "再分析", "scope_id": "scope-c"})
        await append(
            "SQL",
            "assistant",
            {
                "scope_confirmed": True,
                "metric_version": "v0.8",
                "data_quality": current_quality,
                "scope_id": "scope-c",
            },
        )
        await append("ERROR", "assistant", {"error": "模型调用失败"})
        await append("COMPLETE", "assistant", {"text": "不完整的结论"})
        failed = await self.client.post(endpoint, json=request("scope-c", "不完整的结论"))
        self.assertEqual(failed.status_code, 409)
