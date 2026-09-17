import unittest

from analytics_agent.merchant.revision import summarize_scope_revision
from test_history_evidence import event


def scope(*, channel=None, activity_start="2026-08-08", activity_end="2026-08-14"):
    return {
        "campaign_id": "summer",
        "baseline": {"start": "2026-08-01", "end": "2026-08-07"},
        "activity": {"start": activity_start, "end": activity_end},
        "variant": None,
        "channel": channel,
        "location_id": None,
        "audience": "dormant",
        "refund_basis": "after_refunds",
    }


def evidence(scope_id, orders, revenue):
    return {
        "scope_confirmed": True,
        "scope_id": scope_id,
        "rows": [
            {
                "period": "activity",
                "completed_orders": orders,
                "buyer_users": orders - 2,
                "net_revenue_cents": revenue,
                "merchant_discount_cents": 12000,
                "contribution_cents": revenue - 20000,
            }
        ],
    }


class RevisionTests(unittest.TestCase):
    def test_single_completed_scope_has_no_revision(self):
        result = summarize_scope_revision(
            [
                event(
                    "TEXT",
                    {"text": "复盘", "scope_id": "one", "review_scope": scope()},
                    role="user",
                ),
                event("SQL", evidence("one", 100, 500000)),
                event("COMPLETE", {"text": "结论"}),
            ]
        )
        self.assertEqual(result["status"], "single_scope")
        self.assertFalse(result["old_conclusion_superseded"])

    def test_changed_scope_supersedes_old_conclusion_and_compares_metrics(self):
        result = summarize_scope_revision(
            [
                event(
                    "TEXT",
                    {"text": "复盘", "scope_id": "one", "review_scope": scope()},
                    role="user",
                ),
                event("SQL", evidence("one", 100, 500000)),
                event("COMPLETE", {"text": "旧结论"}),
                event(
                    "DECISION",
                    {"status": "committed", "owner": "运营"},
                    role="user",
                ),
                event(
                    "TEXT",
                    {
                        "text": "只看直播渠道",
                        "scope_id": "two",
                        "review_scope": scope(channel="直播"),
                    },
                    role="user",
                ),
                event("SQL", evidence("two", 80, 420000)),
                event("COMPLETE", {"text": "新结论"}),
            ]
        )
        self.assertEqual(result["status"], "revised")
        self.assertEqual(result["changed_fields"][0]["label"], "渠道")
        self.assertEqual(result["changed_fields"][0]["previous"], "全部")
        self.assertEqual(result["changed_fields"][0]["current"], "直播")
        orders = next(
            item for item in result["metric_changes"] if item["key"] == "completed_orders"
        )
        self.assertEqual(orders["delta"], -20)
        self.assertTrue(result["old_conclusion_superseded"])
        self.assertTrue(result["previous_decision_superseded"])
        self.assertEqual(len(result["current_answer_sha256"]), 64)

    def test_incomplete_recalculation_does_not_invalidate_last_completed_scope(self):
        result = summarize_scope_revision(
            [
                event(
                    "TEXT",
                    {"text": "复盘", "scope_id": "one", "review_scope": scope()},
                    role="user",
                ),
                event("SQL", evidence("one", 100, 500000)),
                event("COMPLETE", {"text": "旧结论"}),
                event(
                    "TEXT",
                    {
                        "text": "改口径",
                        "scope_id": "two",
                        "review_scope": scope(channel="直播"),
                    },
                    role="user",
                ),
            ]
        )
        self.assertEqual(result["status"], "single_scope")
        self.assertFalse(result["old_conclusion_superseded"])
