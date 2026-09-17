import tempfile
import unittest
from pathlib import Path

import orjson
from analytics_agent.merchant.engine import MerchantQueryEngine
from analytics_agent.merchant.importer import import_snapshot
from analytics_agent.merchant.readonly import SnapshotReader
from analytics_agent.merchant.scope import (
    ReviewScope,
    coverage_query,
    quality_query,
    timeline_query,
)
from pydantic import ValidationError
from test_importer import exports


def scope_data():
    return {
        "campaign_id": "c",
        "baseline": {"start": "2026-07-31", "end": "2026-07-31"},
        "activity": {"start": "2026-08-01", "end": "2026-08-01"},
    }


class ScopeTests(unittest.TestCase):
    def test_tail_only_counts_orders_matching_their_own_prior_exposure_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            files = exports()
            files["orders"] += (
                b"tail_valid,u1,2026-08-03T10:10:00,c,A,push,store_1,dormant,completed,1000,0,0,0,300\n"
                b"tail_variant,u1,2026-08-03T10:11:00,c,B,push,store_1,dormant,completed,1000,0,0,0,300\n"
                b"tail_channel,u1,2026-08-03T10:12:00,c,A,sms,store_1,dormant,completed,1000,0,0,0,300\n"
                b"tail_location,u1,2026-08-03T10:13:00,c,A,push,store_2,dormant,completed,1000,0,0,0,300\n"
                b"tail_audience,u1,2026-08-03T10:14:00,c,A,push,store_1,new,completed,1000,0,0,0,300\n"
            )
            snapshot = import_snapshot(files, Path(directory))
            evidence = orjson.loads(
                MerchantQueryEngine(Path(snapshot["path"]))
                .comparison_tool()
                .invoke(
                    {
                        "scope": scope_data()
                        | {
                            "activity": {"start": "2026-08-01", "end": "2026-08-02"},
                        },
                    }
                )
            )
            self.assertEqual(
                evidence["attribution_tail"],
                {
                    "completed_orders": 1,
                    "buyer_users": 1,
                    "net_revenue_cents": 1000,
                    "contribution_cents": 700,
                    "candidate_orders": 5,
                    "orders_without_matching_exposure": 4,
                    "orders_outside_exposure_window": 0,
                },
            )

    def test_attribution_tail_is_exposure_linked_and_not_added_to_activity_target(self):
        with tempfile.TemporaryDirectory() as directory:
            files = exports()
            files["orders"] += (
                b"tail1,u1,2026-08-03T10:05:00,c,A,push,store_1,dormant,completed,1500,0,0,0,300\n"
                b"tail2,u2,2026-08-05T11:05:00,c,B,sms,store_2,dormant,completed,1600,0,0,0,300\n"
            )
            snapshot = import_snapshot(files, Path(directory))
            result = orjson.loads(
                MerchantQueryEngine(Path(snapshot["path"]))
                .comparison_tool()
                .invoke(
                    {
                        "scope": scope_data()
                        | {"activity": {"start": "2026-08-01", "end": "2026-08-02"}},
                    }
                )
            )
            self.assertEqual(result["rows"][1]["completed_orders"], 2)
            self.assertEqual(
                result["attribution_tail"],
                {
                    "completed_orders": 1,
                    "buyer_users": 1,
                    "net_revenue_cents": 1500,
                    "contribution_cents": 1200,
                    "candidate_orders": 2,
                    "orders_without_matching_exposure": 0,
                    "orders_outside_exposure_window": 1,
                },
            )
            self.assertEqual(result["attribution_days_configured"], 3)
            self.assertEqual(result["target"]["actual_value"], 1)

            partial = orjson.loads(
                MerchantQueryEngine(Path(snapshot["path"]))
                .comparison_tool()
                .invoke(
                    {
                        "scope": scope_data(),
                    }
                )
            )
            self.assertEqual(partial["attribution_tail_status"], "requires_campaign_end")
            self.assertIsNone(partial["attribution_tail"])

    def test_ordered_path_excludes_out_of_order_and_unlinked_purchases(self):
        with tempfile.TemporaryDirectory() as directory:
            files = exports()
            files["events"] += (
                b"e4,u1,2026-08-01T10:02:00,offer_claim,c,A,push,store_1,dormant\n"
                b"e5,u1,2026-08-01T10:03:00,activation,c,A,push,store_1,dormant\n"
                b"e6,u2,2026-08-01T10:59:00,activation,c,B,sms,store_2,dormant\n"
                b"e7,u2,2026-08-01T11:01:00,landing_view,c,B,sms,store_2,dormant\n"
                b"e8,u2,2026-08-01T11:02:00,offer_claim,c,B,sms,store_2,dormant\n"
            )
            snapshot = import_snapshot(files, Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]))
            result = orjson.loads(engine.comparison_tool().invoke({"scope": scope_data()}))
            by_variant = {row["variant"]: row for row in result["funnel"]}
            self.assertEqual(by_variant["A"]["activated_users"], 1)
            self.assertEqual(by_variant["A"]["path_buyer_users"], 1)
            self.assertEqual(by_variant["A"]["activated_without_path_purchase_users"], 0)
            self.assertEqual(by_variant["A"]["activation_to_path_purchase_rate"], 1)
            self.assertEqual(by_variant["B"]["activated_users"], 0)
            self.assertEqual(by_variant["B"]["path_buyer_users"], 0)
            self.assertIsNone(by_variant["B"]["activation_to_path_purchase_rate"])
            self.assertEqual(by_variant["B"]["buyer_users"], 1)

    def test_quality_readout_identifies_unlinked_buyer_and_cross_variant_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            files = exports()
            files["events"] += b"e4,u1,2026-08-01T10:02:00,exposure,c,B,sms,store_2,dormant\n"
            files["orders"] += (
                b"d,u4,2026-08-01T12:10:00,c,A,push,store_1,dormant,completed,1000,0,0,0,300\n"
            )
            snapshot = import_snapshot(files, Path(directory))
            sql, parameters = quality_query(ReviewScope.model_validate(scope_data()))
            result = SnapshotReader(Path(snapshot["path"])).query(sql, parameters)
            self.assertNotIn("error", result)
            self.assertEqual(
                result["rows"][0],
                {
                    "exposed_users": 2,
                    "buyer_users": 3,
                    "cross_variant_exposed_users": 1,
                    "buyers_without_prior_exposure": 1,
                    "buyers_without_matching_variant_exposure": 1,
                    "buyers_without_matching_channel_exposure": 1,
                    "buyers_without_matching_location_exposure": 1,
                    "buyers_without_matching_audience_exposure": 1,
                    "buyers_without_matching_selected_exposure": 1,
                },
            )
            selected = ReviewScope.model_validate(scope_data() | {"variant": "A"})
            selected_sql, selected_parameters = quality_query(selected)
            selected_result = SnapshotReader(Path(snapshot["path"])).query(
                selected_sql,
                selected_parameters,
            )
            self.assertEqual(selected_result["rows"][0]["cross_variant_exposed_users"], 1)

    def test_dimension_mismatch_is_detected_even_with_prior_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            files = exports()
            files["orders"] = files["orders"].replace(
                b"b,u2,2026-08-01T11:10:00,c,B,sms,store_2,dormant,completed",
                b"b,u2,2026-08-01T11:10:00,c,A,push,store_1,dormant,completed",
            )
            snapshot = import_snapshot(files, Path(directory))
            sql, parameters = quality_query(ReviewScope.model_validate(scope_data()))
            result = SnapshotReader(Path(snapshot["path"])).query(sql, parameters)
            self.assertNotIn("error", result)
            quality = result["rows"][0]
            self.assertEqual(quality["buyers_without_prior_exposure"], 0)
            self.assertEqual(quality["buyers_without_matching_variant_exposure"], 1)
            self.assertEqual(quality["buyers_without_matching_channel_exposure"], 1)
            self.assertEqual(quality["buyers_without_matching_location_exposure"], 1)
            self.assertEqual(quality["buyers_without_matching_audience_exposure"], 0)

    def test_joint_labels_must_match_one_prior_exposure_not_two_different_events(self):
        with tempfile.TemporaryDirectory() as directory:
            files = exports()
            files["events"] += b"e4,u2,2026-08-01T11:02:00,exposure,c,A,push,store_1,dormant\n"
            files["orders"] = files["orders"].replace(
                b"b,u2,2026-08-01T11:10:00,c,B,sms,store_2,dormant,completed",
                b"b,u2,2026-08-01T11:10:00,c,B,push,store_2,dormant,completed",
            )
            snapshot = import_snapshot(files, Path(directory))
            sql, parameters = quality_query(
                ReviewScope.model_validate(scope_data()),
                matching_dimensions=("channel", "location_id"),
            )
            result = SnapshotReader(Path(snapshot["path"])).query(sql, parameters)
            self.assertNotIn("error", result)
            quality = result["rows"][0]
            self.assertEqual(quality["buyers_without_matching_channel_exposure"], 0)
            self.assertEqual(quality["buyers_without_matching_location_exposure"], 0)
            self.assertEqual(quality["buyers_without_matching_selected_exposure"], 1)

    def test_timeline_keeps_zero_order_days_and_matches_headline_total(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = import_snapshot(exports(), Path(directory))
            scope = ReviewScope.model_validate(
                scope_data()
                | {
                    "activity": {"start": "2026-08-01", "end": "2026-08-02"},
                }
            )
            sql, parameters = timeline_query(scope)
            result = SnapshotReader(Path(snapshot["path"])).query(sql, parameters)
            self.assertNotIn("error", result)
            self.assertEqual(len(result["rows"]), 3)
            self.assertEqual([row["completed_orders"] for row in result["rows"]], [0, 2, 0])
            self.assertEqual(sum(row["contribution_cents"] for row in result["rows"]), 3700)

    def test_coverage_counts_observed_days_without_claiming_export_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = import_snapshot(exports(), Path(directory))
            scope = ReviewScope.model_validate(
                scope_data()
                | {
                    "activity": {"start": "2026-08-01", "end": "2026-08-02"},
                }
            )
            sql, parameters = coverage_query(scope)
            result = SnapshotReader(Path(snapshot["path"])).query(sql, parameters)
            self.assertNotIn("error", result)
            self.assertEqual(
                result["rows"][0],
                {
                    "baseline_days": 1,
                    "activity_days": 2,
                    "baseline_days_with_orders": 0,
                    "activity_days_with_orders": 1,
                    "activity_days_with_exposure": 1,
                    "last_observed_activity_order_date": "2026-08-01",
                },
            )
            aggregate = orjson.loads(
                MerchantQueryEngine(Path(snapshot["path"]))
                .comparison_tool()
                .invoke(
                    {
                        "scope": scope_data()
                        | {
                            "activity": {"start": "2026-08-01", "end": "2026-08-02"},
                        }
                    }
                )
            )
            self.assertEqual(aggregate["data_coverage"], result["rows"][0])
            self.assertIn(
                "data_coverage", {entry["kind"] for entry in aggregate["analysis_manifest"]}
            )
            coverage_id = next(
                entry["evidence_id"]
                for entry in aggregate["analysis_manifest"]
                if entry["kind"] == "data_coverage"
            )
            self.assertIn(coverage_id, aggregate["related_evidence_ids"])

    def test_activity_slice_scopes_outcome_journey_and_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            files = exports()
            files["events"] += (
                b"e4,u4,2026-08-02T10:00:00,exposure,c,A,sms,store_2,dormant\n"
                b"e5,u1,2026-08-01T10:02:00,landing_view,c,A,sms,store_2,dormant\n"
            )
            files["orders"] += (
                b"d,u4,2026-08-02T10:10:00,c,A,sms,store_2,dormant,completed,10000,0,0,0,2000\n"
            )
            files["costs"] += b"k2,c,2026-08-02,A,sms,media,900\n"
            snapshot = import_snapshot(files, Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]))
            result = orjson.loads(engine.comparison_tool().invoke({"scope": scope_data()}))
            self.assertEqual(result["rows"][1]["completed_orders"], 2)
            self.assertEqual(result["funnel"][0]["exposed_users"], 1)
            self.assertEqual(result["funnel"][0]["landing_users"], 1)
            self.assertEqual(result["funnel"][0]["completed_orders"], 1)
            self.assertEqual(result["segments"]["channel"][0]["total_groups"], 2)
            self.assertEqual(sum(row["activity_cost_cents"] for row in result["costs"]), 500)
            engine.confirmed_scope = ReviewScope.model_validate(scope_data())
            drill = orjson.loads(
                engine.get_tools()[-1].invoke(
                    {
                        "dimension": "variant",
                        "reason": "定位路径缺口",
                    }
                )
            )
            self.assertEqual(drill["rows"][0]["exposed_users"], 1)
            self.assertEqual(drill["rows"][0]["order_contribution_cents"], 2500)
            self.assertEqual(sum(row["activity_cost_cents"] for row in drill["costs"]), 500)

    def test_activity_outcome_excludes_other_campaign_on_same_day(self):
        with tempfile.TemporaryDirectory() as directory:
            files = exports()
            files["campaigns"] += (
                b"other,other campaign,reactivate,2026-08-01,2026-08-02,completed_orders,1,3\n"
            )
            files["orders"] += (
                b"d,u4,2026-08-01T12:10:00,other,A,push,store_1,dormant,completed,10000,0,0,0,2000\n"
            )
            snapshot = import_snapshot(files, Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]))
            result = orjson.loads(engine.comparison_tool().invoke({"scope": scope_data()}))
            self.assertEqual(result["rows"][1]["completed_orders"], 2)

    def test_agent_cannot_change_confirmed_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = import_snapshot(exports(), Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]))
            engine.confirmed_scope = ReviewScope.model_validate(scope_data())
            result = orjson.loads(
                engine.comparison_tool().invoke(
                    {"scope": scope_data() | {"refund_basis": "before_refunds"}}
                )
            )
            self.assertEqual(result["error"], "scope_requires_user_confirmation")
            self.assertEqual(engine.evidence, [])

    def test_correction_recalculates_and_versions_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            files = exports()
            files["orders"] = files["orders"].replace(
                b"2026-08-01T11:10:00,c,B", b"2026-07-31T11:10:00,,B"
            )
            snapshot = import_snapshot(files, Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]))
            tool = engine.comparison_tool()
            first = orjson.loads(tool.invoke({"scope": scope_data()}))
            changed = scope_data() | {"refund_basis": "before_refunds"}
            second = orjson.loads(tool.invoke({"scope": changed}))
            self.assertEqual([r["contribution_cents"] for r in first["rows"]], [1200, 2500])
            self.assertEqual([r["contribution_cents"] for r in second["rows"]], [1200, 3500])
            self.assertNotEqual(first["scope_id"], second["scope_id"])
            self.assertNotEqual(first["evidence_id"], second["evidence_id"])
            self.assertEqual(len(engine.evidence), 20)
            self.assertEqual(len(second["analysis_manifest"]), 6)
            self.assertEqual(
                [entry["kind"] for entry in second["evidence_manifest"]],
                ["outcome", "funnel", "cost", "target"],
            )
            self.assertTrue(
                all(
                    entry.get("sql") and entry.get("scope_id")
                    for entry in second["evidence_manifest"]
                )
            )
            self.assertIn("退款前", second["warnings"][-1])
            self.assertEqual(engine.evidence[0]["scope"]["refund_basis"], "after_refunds")

    def test_invalid_or_overlapping_dates_rejected(self):
        bad = scope_data()
        bad["baseline"] = bad["activity"]
        with self.assertRaises(ValidationError):
            ReviewScope.model_validate(bad)

    def test_filter_is_bound_parameter_not_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = import_snapshot(exports(), Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]))
            result = orjson.loads(
                engine.comparison_tool().invoke(
                    {"scope": scope_data() | {"location_id": "s' OR 1=1 --"}}
                )
            )
            self.assertEqual([row["completed_orders"] for row in result["rows"]], [0, 0])
            self.assertTrue(any("没有匹配订单" in w for w in result["warnings"]))

    def test_unequal_period_length_warns(self):
        data = scope_data()
        data["baseline"]["start"] = "2026-07-30"
        self.assertTrue(any("天数不同" in w for w in ReviewScope.model_validate(data).warnings))
