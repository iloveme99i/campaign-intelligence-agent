import tempfile
import unittest
from pathlib import Path

import orjson
from analytics_agent.merchant.engine import MerchantQueryEngine, build_analysis_brief
from analytics_agent.merchant.importer import import_snapshot
from analytics_agent.merchant.scope import Period, ReviewScope, ReviewTask
from test_importer import exports


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        snapshot = import_snapshot(exports(), Path(self.directory.name))
        self.snapshot_path = Path(snapshot["path"])
        self.engine = MerchantQueryEngine(self.snapshot_path, max_queries=2, synthetic=True)
        self.tools = {tool.name: tool for tool in self.engine.get_tools()}

    def test_real_tool_call_has_replayable_evidence(self):
        sql = "SELECT SUM(contribution_cents) AS total FROM order_metrics"
        result = orjson.loads(self.tools["execute_sql"].invoke({"sql": sql}))
        self.assertEqual(result["rows"], [{"total": 3700}])
        self.assertEqual(self.engine.evidence[0]["sql"], sql)
        self.assertEqual(result["evidence_id"], self.engine.evidence[0]["evidence_id"])
        self.assertEqual(result["provenance"], "synthetic")
        detached = self.engine.evidence
        detached[0]["rows"].clear()
        self.assertEqual(self.engine.evidence[0]["rows"], [{"total": 3700}])

    def test_errors_consume_budget_and_are_recorded(self):
        for _ in range(2):
            self.assertIn(
                "error",
                orjson.loads(self.tools["execute_sql"].invoke({"sql": "DROP TABLE orders"})),
            )
        result = orjson.loads(self.tools["execute_sql"].invoke({"sql": "SELECT 1"}))
        self.assertEqual(result["error"], "query_budget_exhausted")
        self.assertEqual(len(self.engine.evidence), 2)

    def test_preview_table_name_is_not_sql(self):
        result = orjson.loads(
            self.tools["preview_table"].invoke({"table": "orders; DROP TABLE items"})
        )
        self.assertEqual(result["error"], "invalid_preview")

    def test_schema_includes_business_metrics(self):
        result = orjson.loads(self.tools["get_schema"].invoke({"table": "order_metrics"}))
        self.assertIn("contribution_cents", result["columns"])
        self.assertEqual(result["rows"], [])

    def test_dimension_diagnosis_requires_confirmed_scope(self):
        scope = ReviewScope(
            campaign_id="c",
            baseline=Period(start="2026-07-25", end="2026-07-31"),
            activity=Period(start="2026-08-01", end="2026-08-07"),
        )
        tool = self.tools["diagnose_dimension"]
        self.assertNotIn("scope", tool.args)
        refused = orjson.loads(
            tool.invoke(
                {
                    "dimension": "channel",
                    "reason": "定位渠道路径差异",
                }
            )
        )
        self.assertEqual(refused["error"], "scope_requires_user_confirmation")

        self.engine.confirmed_scope = scope
        result = orjson.loads(
            tool.invoke(
                {
                    "dimension": "channel",
                    "reason": "定位渠道路径差异",
                }
            )
        )
        self.assertEqual([row["dimension_value"] for row in result["rows"]], ["push", "sms"])
        self.assertTrue(result["evidence_id"].startswith("q_"))
        self.assertEqual(result["diagnostic_reason"], "定位渠道路径差异")
        self.assertTrue(result["related_evidence_ids"])

        variant_engine = MerchantQueryEngine(self.snapshot_path, max_queries=3, synthetic=True)
        variant_engine.confirmed_scope = scope
        variant_tool = {item.name: item for item in variant_engine.get_tools()}[
            "diagnose_dimension"
        ]
        variant_result = orjson.loads(
            variant_tool.invoke(
                {
                    "dimension": "variant",
                    "reason": "比较实验组效率",
                }
            )
        )
        self.assertEqual(variant_result["experiment"]["status"], "observational_readout")
        self.assertEqual(variant_result["experiment"]["metric"], "purchase_per_exposure")
        contract = variant_result["decision_contract"]["experiment"]
        self.assertEqual(contract["metric"], "purchase_per_exposure")
        comparison = contract["comparisons"][0]
        self.assertAlmostEqual(
            comparison["treatment_rate_pct"] - comparison["control_rate_pct"],
            comparison["difference_pp"],
        )

    def test_dimension_diagnosis_requires_business_reason(self):
        scope = ReviewScope(
            campaign_id="c",
            baseline=Period(start="2026-07-25", end="2026-07-31"),
            activity=Period(start="2026-08-01", end="2026-08-07"),
        )
        self.engine.confirmed_scope = scope
        result = orjson.loads(
            self.tools["diagnose_dimension"].invoke({"dimension": "channel", "reason": "  "})
        )
        self.assertEqual(result["error"], "diagnostic_reason_required")

    def test_analysis_brief_keeps_headline_and_withholds_diagnostic_rows(self):
        scope = ReviewScope(
            campaign_id="c",
            baseline=Period(start="2026-07-25", end="2026-07-31"),
            activity=Period(start="2026-08-01", end="2026-08-07"),
        )
        task = ReviewTask(
            decision_intent="adjust",
            risk_focus="cost",
            business_context="预算不能增加",
        )
        engine = MerchantQueryEngine(self.snapshot_path, max_queries=12, synthetic=True)
        engine.confirmed_scope = scope
        result = orjson.loads(engine.comparison_tool().invoke({"scope": scope.model_dump()}))

        brief = build_analysis_brief(result, task)

        self.assertEqual(brief["analysis_stage"], "headline_complete_diagnosis_required")
        self.assertEqual(brief["task_id"], task.task_id)
        self.assertIn("funnel_totals", brief)
        self.assertEqual(brief["path_diagnostics"]["status"], "evaluated")
        self.assertTrue(brief["path_diagnostics"]["evidence_id"])
        self.assertTrue(brief["decision_contract"]["immutable"])
        self.assertEqual(
            brief["decision_contract"]["next_experiment"]["required_primary_metric"],
            "purchase_per_exposure",
        )
        self.assertNotIn("funnel", brief)
        self.assertNotIn("costs", brief)
        self.assertEqual(
            set(brief["available_diagnostics"]), {"variant", "channel", "location_id", "audience"}
        )
        path_result = {
            **result,
            "path_diagnostics": {
                "status": "evaluated",
                "evidence_id": "q_path",
                "rows": [
                    {
                        "variant": "A",
                        "stage_key": "activation_to_path_purchase",
                        "stage_label": "行动→完整购买",
                        "from_users": 20,
                        "to_users": 5,
                        "continuation_rate": 0.25,
                        "not_continued_users": 15,
                    },
                    {
                        "variant": "B",
                        "stage_key": "activation_to_path_purchase",
                        "stage_label": "行动→完整购买",
                        "from_users": 24,
                        "to_users": 8,
                        "continuation_rate": 1 / 3,
                        "not_continued_users": 16,
                    },
                ],
            },
        }
        path_brief = build_analysis_brief(
            path_result,
            ReviewTask(
                decision_intent="continue",
                risk_focus="path",
                business_context="确认承接节点",
            ),
        )
        self.assertEqual(
            path_brief["decision_contract"]["next_experiment"]["required_primary_metric"],
            "path_purchase_per_activation",
        )

    def test_comparison_estimates_incrementality_only_from_valid_optional_panel(self):
        files = exports()
        lines = ["panel_unit,campaign_id,period,metric,treated,post,outcome,interference_reviewed"]
        for treated, prefix in ((0, "c"), (1, "t")):
            for unit in range(4):
                for period_index, period in enumerate(
                    ("2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01")
                ):
                    outcome = 10 + period_index + unit / 10
                    if period_index == 3:
                        outcome += 3 if treated else 1
                    lines.append(
                        f"{prefix}{unit},c,{period},completed_orders,{treated},"
                        f"{int(period_index == 3)},{outcome},1"
                    )
        files["incrementality"] = ("\n".join(lines) + "\n").encode()
        with tempfile.TemporaryDirectory() as directory:
            snapshot = import_snapshot(files, Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]), max_queries=20)
            scope = ReviewScope(
                campaign_id="c",
                baseline=Period(start="2026-07-30", end="2026-07-31"),
                activity=Period(start="2026-08-01", end="2026-08-02"),
            )
            engine.confirmed_scope = scope
            result = orjson.loads(engine.comparison_tool().invoke({"scope": scope.model_dump()}))

            filtered_scope = scope.model_copy(update={"channel": "push"})
            filtered_engine = MerchantQueryEngine(Path(snapshot["path"]), max_queries=20)
            filtered_engine.confirmed_scope = filtered_scope
            filtered = orjson.loads(
                filtered_engine.comparison_tool().invoke({"scope": filtered_scope.model_dump()})
            )

            shifted_scope = scope.model_copy(
                update={"activity": Period(start="2026-08-02", end="2026-08-03")}
            )
            shifted_engine = MerchantQueryEngine(Path(snapshot["path"]), max_queries=20)
            shifted_engine.confirmed_scope = shifted_scope
            shifted = orjson.loads(
                shifted_engine.comparison_tool().invoke({"scope": shifted_scope.model_dump()})
            )

        self.assertEqual(result["incrementality_readiness"]["status"], "did_ready")
        self.assertEqual(result["incrementality"]["status"], "identified")
        self.assertEqual(result["incrementality"]["estimates"][0]["estimate"], 2.0)
        self.assertTrue(result["incrementality"]["estimates"][0]["decision_ready"])
        self.assertIn(
            "incrementality",
            {item["kind"] for item in result["evidence_manifest"]},
        )
        self.assertEqual(filtered["incrementality"]["status"], "not_estimated")
        self.assertIn("筛选", filtered["incrementality"]["detail"])
        self.assertNotIn(
            "incrementality",
            {item["kind"] for item in filtered["evidence_manifest"]},
        )
        self.assertEqual(shifted["incrementality"]["status"], "not_estimated")
        self.assertIn("周期", shifted["incrementality"]["detail"])

    def test_channel_scope_marks_shared_campaign_cost_as_unallocated(self):
        files = exports()
        files["costs"] = files["costs"].replace(b",push,media,500", b",all,media,500")
        with tempfile.TemporaryDirectory() as directory:
            snapshot = import_snapshot(files, Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]), max_queries=20)
            scoped = scope = ReviewScope(
                campaign_id="c",
                baseline=Period(start="2026-07-25", end="2026-07-31"),
                activity=Period(start="2026-08-01", end="2026-08-07"),
                channel="push",
            )
            engine.confirmed_scope = scoped
            result = orjson.loads(engine.comparison_tool().invoke({"scope": scope.model_dump()}))
            brief = build_analysis_brief(
                result,
                ReviewTask(
                    decision_intent="adjust",
                    risk_focus="cost",
                    business_context="仅调整推送渠道",
                ),
            )

        self.assertEqual(result["cost_boundary"]["status"], "shared_costs_unallocated")
        self.assertEqual(result["cost_boundary"]["shared_cost_cents"], 500)
        self.assertIsNone(brief["activity_cost_cents"])
        self.assertIn("不能将其全部归属于当前渠道", brief["cost_boundary"]["detail"])
        self.assertIn(
            "当前无法判断经营结果能否覆盖活动投入", (brief["decision_contract"]["cost_statement"])
        )

    def test_variant_and_location_scopes_do_not_claim_unallocated_roi(self):
        files = exports()
        files["costs"] += b"k2,c,2026-08-01,all,all,creative,300\n"
        with tempfile.TemporaryDirectory() as directory:
            snapshot = import_snapshot(files, Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]), max_queries=20)
            basis = {
                "campaign_id": "c",
                "baseline": {"start": "2026-07-25", "end": "2026-07-31"},
                "activity": {"start": "2026-08-01", "end": "2026-08-07"},
            }
            variant_result = orjson.loads(
                engine.comparison_tool().invoke(
                    {
                        "scope": basis | {"variant": "A"},
                    }
                )
            )
            location_result = orjson.loads(
                engine.comparison_tool().invoke(
                    {
                        "scope": basis | {"location_id": "store_1"},
                    }
                )
            )
        self.assertEqual(variant_result["cost_boundary"]["status"], "shared_costs_unallocated")
        self.assertEqual(variant_result["cost_boundary"]["shared_cost_cents"], 300)
        self.assertEqual(location_result["cost_boundary"]["status"], "shared_costs_unallocated")
        self.assertEqual(location_result["cost_boundary"]["shared_cost_cents"], 800)
