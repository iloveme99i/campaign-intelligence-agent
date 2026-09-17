import io
import tempfile
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path

import orjson
from analytics_agent.merchant.engine import MerchantQueryEngine
from analytics_agent.merchant.examples import SCENARIOS, example_archive, example_exports
from analytics_agent.merchant.importer import import_snapshot
from analytics_agent.merchant.readonly import SnapshotReader


class ExampleTests(unittest.TestCase):
    def test_deterministic_import_contains_cross_industry_experiment_and_baseline_evidence(self):
        self.assertEqual(example_archive(), example_archive())
        with tempfile.TemporaryDirectory() as directory:
            result = import_snapshot(example_exports(), Path(directory))
            reader = SnapshotReader(Path(result["path"]))
            campaigns = reader.query(
                "SELECT campaign_id, campaign_name FROM campaigns ORDER BY start_date"
            )["rows"]
            self.assertEqual(
                [row["campaign_id"] for row in campaigns], [s.campaign_id for s in SCENARIOS]
            )
            self.assertEqual(
                [row["campaign_name"] for row in campaigns],
                ["字节系 · 全域大促", "腾讯游戏 · 回流任务季", "腾讯零售 · 私域联动"],
            )
            self.assertEqual(
                {variant for scenario in SCENARIOS for variant in scenario.variants},
                {"单件直降", "直播组合包", "登录礼包", "阶段任务", "单券承接", "内容与券联动"},
            )
            for scenario in SCENARIOS:
                funnel = reader.query(
                    "SELECT variant, SUM(exposed_users) exposed_users, "
                    "SUM(buyer_users) buyer_users FROM campaign_funnel "
                    "WHERE campaign_id=:campaign_id GROUP BY variant ORDER BY variant",
                    {"campaign_id": scenario.campaign_id},
                )["rows"]
                self.assertEqual(len(funnel), 2)
                self.assertEqual(
                    sum(row["exposed_users"] for row in funnel), scenario.exposed_users
                )
                self.assertGreater(sum(row["buyer_users"] for row in funnel), 0)
                baseline = reader.query(
                    "SELECT COUNT(*) completed_orders FROM order_metrics "
                    "WHERE campaign_id='' AND business_date BETWEEN :start AND :end",
                    {
                        "start": (scenario.start - timedelta(days=7)).isoformat(),
                        "end": (scenario.start - timedelta(days=1)).isoformat(),
                    },
                )["rows"][0]
                self.assertEqual(baseline["completed_orders"], scenario.baseline_orders)
                panel = reader.query(
                    "SELECT COUNT(*) row_count, COUNT(DISTINCT panel_unit) unit_count "
                    "FROM incrementality WHERE campaign_id=:campaign_id",
                    {"campaign_id": scenario.campaign_id},
                )["rows"][0]
                self.assertEqual(panel, {"row_count": 256, "unit_count": 16})

    def test_all_demo_campaigns_have_replayable_multilens_and_linked_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = import_snapshot(example_exports(), Path(directory))
            for scenario in SCENARIOS:
                engine = MerchantQueryEngine(Path(snapshot["path"]), synthetic=True)
                result = orjson.loads(
                    engine.comparison_tool().invoke(
                        {
                            "scope": {
                                "campaign_id": scenario.campaign_id,
                                "baseline": {
                                    "start": (scenario.start - timedelta(days=7)).isoformat(),
                                    "end": (scenario.start - timedelta(days=1)).isoformat(),
                                },
                                "activity": {
                                    "start": scenario.start.isoformat(),
                                    "end": (scenario.start + timedelta(days=6)).isoformat(),
                                },
                            }
                        }
                    )
                )
                self.assertNotIn("error", result)
                self.assertEqual(result["metric_version"], "v0.8")
                self.assertEqual(result["data_quality"]["buyers_without_prior_exposure"], 0)
                self.assertEqual(result["data_quality"]["cross_variant_exposed_users"], 0)
                self.assertEqual(
                    sum(row["path_buyer_users"] for row in result["funnel"]),
                    result["data_quality"]["buyer_users"],
                )
                self.assertEqual(result["path_diagnostics"]["status"], "evaluated")
                self.assertEqual(
                    result["path_diagnostics"]["evidence_id"],
                    next(
                        item["evidence_id"]
                        for item in result["evidence_manifest"]
                        if item["kind"] == "funnel"
                    ),
                )
                for row in result["funnel"]:
                    self.assertEqual(
                        row["activated_without_path_purchase_users"],
                        row["activated_users"] - row["path_buyer_users"],
                    )
                    if row["activated_users"]:
                        self.assertAlmostEqual(
                            row["activation_to_path_purchase_rate"],
                            row["path_buyer_users"] / row["activated_users"],
                        )
                if scenario.campaign_id == "tencent_game_return_ops":
                    stage = next(row for row in result["funnel"] if row["variant"] == "阶段任务")
                    self.assertEqual(stage["activated_users"], 237)
                    self.assertEqual(stage["path_buyer_users"], 46)
                    self.assertEqual(stage["activated_without_path_purchase_users"], 191)
                self.assertEqual(len(result["timeline"]), 14)
                self.assertEqual(
                    set(result["segments"]), {"variant", "channel", "location_id", "audience"}
                )
                self.assertEqual(len(result["analysis_manifest"]), 7)
                self.assertTrue(
                    all(
                        entry.get("sql") and entry.get("evidence_id")
                        for entry in result["analysis_manifest"]
                    )
                )
                self.assertEqual(result["incrementality"]["status"], "identified")
                estimates = {
                    estimate["metric"]: estimate
                    for estimate in result["incrementality"]["estimates"]
                }
                self.assertEqual(
                    set(estimates),
                    {
                        "completed_orders",
                        "buyer_users",
                        "net_revenue_cents",
                        "contribution_cents",
                    },
                )
                self.assertTrue(all(estimate["decision_ready"] for estimate in estimates.values()))
                self.assertAlmostEqual(estimates["completed_orders"]["estimate"], 11.5)

    def test_archive_is_labeled_and_templates_are_empty(self):
        with zipfile.ZipFile(io.BytesIO(example_archive(templates_only=True))) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "campaigns.csv",
                    "events.csv",
                    "orders.csv",
                    "costs.csv",
                    "incrementality.csv",
                    "README.txt",
                },
            )
            self.assertIn("数据完全合成", archive.read("README.txt").decode())
            self.assertIn("四级标准化旅程", archive.read("README.txt").decode())
            self.assertEqual(archive.read("campaigns.csv").count(b"\n"), 1)
