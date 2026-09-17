"""Describe campaign choices and available scope without an LLM."""

from analytics_agent.merchant.readonly import SnapshotReader


def describe_snapshot(reader: SnapshotReader) -> dict:
    queries = {
        "campaigns": """SELECT c.*,
          (SELECT COUNT(DISTINCT user_key) FROM events e
           WHERE e.campaign_id=c.campaign_id AND e.event_name='exposure') AS exposed_users,
          (SELECT COUNT(*) FROM order_metrics o WHERE o.campaign_id=c.campaign_id) AS completed_orders,
          (SELECT COALESCE(SUM(amount_cents),0) FROM costs k WHERE k.campaign_id=c.campaign_id) AS activity_cost_cents
          FROM campaigns c ORDER BY end_date DESC, campaign_id""",
        "channels": "SELECT DISTINCT channel FROM events WHERE channel<>'' ORDER BY channel",
        "locations": "SELECT DISTINCT location_id FROM events WHERE location_id<>'' ORDER BY location_id",
        "audiences": "SELECT DISTINCT audience FROM events WHERE audience<>'' ORDER BY audience",
        "variants": "SELECT DISTINCT variant FROM events WHERE variant<>'' ORDER BY variant",
        "summary": """SELECT
          (SELECT COUNT(*) FROM campaigns) AS campaign_count,
          (SELECT COUNT(*) FROM events) AS event_count,
          (SELECT COUNT(*) FROM order_metrics) AS completed_order_count""",
    }
    results = {}
    for name, sql in queries.items():
        result = reader.query(sql)
        if result.get("error") or result["truncated"]:
            raise ValueError("无法完整读取活动数据范围")
        results[name] = result["rows"]
    return {
        **results["summary"][0],
        "campaigns": results["campaigns"],
        "channels": [row["channel"] for row in results["channels"]],
        "locations": [row["location_id"] for row in results["locations"]],
        "audiences": [row["audience"] for row in results["audiences"]],
        "variants": [row["variant"] for row in results["variants"]],
        "event_basis": "distinct_user",
        "order_basis": "completed",
    }
