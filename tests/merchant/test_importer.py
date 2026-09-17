import tempfile
import unittest
from pathlib import Path

from analytics_agent.merchant.importer import ImportValidationError, import_snapshot, parse_exports
from analytics_agent.merchant.readonly import SnapshotReader


def exports():
    return {
        "campaigns": b"campaign_id,campaign_name,objective,start_date,end_date,primary_metric,target_value,attribution_days\nc,member recall,reactivate,2026-08-01,2026-08-02,conversion_rate,0.1,3\n",
        "events": b"event_id,user_key,event_time,event_name,campaign_id,variant,channel,location_id,audience\ne1,u1,2026-08-01T10:00:00,exposure,c,A,push,store_1,dormant\ne2,u1,2026-08-01T10:01:00,landing_view,c,A,push,store_1,dormant\ne3,u2,2026-08-01T11:00:00,exposure,c,B,sms,store_2,dormant\n",
        "orders": b"order_id,user_key,paid_at,campaign_id,variant,channel,location_id,audience,status,gross_cents,merchant_discount_cents,platform_discount_cents,refund_cents,cost_cents\na,u1,2026-08-01T10:10:00,c,A,push,store_1,dormant,completed,10000,2000,1500,1000,4500\nb,u2,2026-08-01T11:10:00,c,B,sms,store_2,dormant,completed,2000,0,0,0,800\nc,u3,2026-08-01T12:00:00,c,B,sms,store_2,dormant,cancelled,1000,0,0,0,400\n",
        "costs": b"cost_id,campaign_id,business_date,variant,channel,cost_type,amount_cents\nk1,c,2026-08-01,A,push,media,500\n",
    }


class ImportTests(unittest.TestCase):
    def test_optional_incrementality_panel_is_validated_and_published(self):
        data = exports()
        data["incrementality"] = (
            b"panel_unit,campaign_id,period,metric,treated,post,outcome,interference_reviewed\n"
            b"store_1,c,2026-07-01,completed_orders,0,0,10,1\n"
            b"store_1,c,2026-08-01,completed_orders,0,1,12,1\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            imported = import_snapshot(data, Path(directory))
            rows = SnapshotReader(Path(imported["path"])).query(
                "SELECT panel_unit, period, treated, post, outcome "
                "FROM incrementality ORDER BY period"
            )["rows"]

        self.assertEqual(imported["row_counts"]["incrementality"], 2)
        self.assertEqual(
            rows,
            [
                {
                    "panel_unit": "store_1",
                    "period": "2026-07-01",
                    "treated": 0,
                    "post": 0,
                    "outcome": 10.0,
                },
                {
                    "panel_unit": "store_1",
                    "period": "2026-08-01",
                    "treated": 0,
                    "post": 1,
                    "outcome": 12.0,
                },
            ],
        )

    def test_missing_optional_panel_creates_an_empty_queryable_table(self):
        with tempfile.TemporaryDirectory() as directory:
            imported = import_snapshot(exports(), Path(directory))
            rows = SnapshotReader(Path(imported["path"])).query("SELECT * FROM incrementality")[
                "rows"
            ]

        self.assertEqual(imported["row_counts"]["incrementality"], 0)
        self.assertEqual(rows, [])

    def test_incrementality_panel_rejects_post_selected_treatment_flag(self):
        data = exports()
        data["incrementality"] = (
            b"panel_unit,campaign_id,period,metric,treated,post,outcome,interference_reviewed\n"
            b"store_1,c,2026-07-01,completed_orders,0,0,10,1\n"
            b"store_1,c,2026-08-01,completed_orders,1,1,12,1\n"
        )

        with self.assertRaisesRegex(ImportValidationError, "treatment assignment"):
            parse_exports(data)

    def test_order_money_semantics_and_cancel_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            imported = import_snapshot(exports(), Path(directory))
            rows = SnapshotReader(Path(imported["path"])).query(
                "SELECT order_id, customer_paid_cents, net_revenue_cents, contribution_cents FROM order_metrics ORDER BY order_id"
            )["rows"]
            self.assertEqual(
                rows,
                [
                    {
                        "order_id": "a",
                        "customer_paid_cents": 6500,
                        "net_revenue_cents": 7000,
                        "contribution_cents": 2500,
                    },
                    {
                        "order_id": "b",
                        "customer_paid_cents": 2000,
                        "net_revenue_cents": 2000,
                        "contribution_cents": 1200,
                    },
                ],
            )

    def test_funnel_counts_distinct_users_without_join_multiplication(self):
        with tempfile.TemporaryDirectory() as directory:
            imported = import_snapshot(exports(), Path(directory))
            rows = SnapshotReader(Path(imported["path"])).query(
                "SELECT variant, exposed_users, landing_users, buyer_users FROM campaign_funnel ORDER BY variant"
            )["rows"]
            self.assertEqual(
                rows,
                [
                    {"variant": "A", "exposed_users": 1, "landing_users": 1, "buyer_users": 1},
                    {"variant": "B", "exposed_users": 1, "landing_users": 0, "buyer_users": 1},
                ],
            )

    def test_idempotent_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            first = import_snapshot(exports(), Path(directory))
            second = import_snapshot(exports(), Path(directory))
            self.assertEqual(first, second)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_invalid_batch_leaves_no_database(self):
        cases = [
            ("campaigns", b"2026-08-01,2026-08-02", b"2026-08-03,2026-08-02"),
            ("campaigns", b"conversion_rate", b"likes"),
            ("events", b"landing_view", b"unknown_event"),
            ("events", b"2026-08-01T10:00:00", b"2026-07-01T10:00:00"),
            ("orders", b"completed,10000", b"paid,10000"),
            ("orders", b"10000,2000,1500", b"1000,2000,1500"),
            ("orders", b"1500,1000,4500", b"1500,9000,4500"),
            ("costs", b",500\n", b",-500\n"),
        ]
        for table, old, new in cases:
            with self.subTest(table=table), tempfile.TemporaryDirectory() as directory:
                data = exports()
                data[table] = data[table].replace(old, new)
                with self.assertRaises(ImportValidationError):
                    import_snapshot(data, Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_missing_file_is_not_assumed_empty(self):
        data = exports()
        del data["costs"]
        with self.assertRaises(ImportValidationError):
            parse_exports(data)

    def test_duplicate_experiment_exposure_is_rejected(self):
        data = exports()
        data["events"] += b"e4,u1,2026-08-01T10:05:00,exposure,c,A,push,store_1,dormant\n"
        with self.assertRaises(ImportValidationError):
            parse_exports(data)


if __name__ == "__main__":
    unittest.main()
