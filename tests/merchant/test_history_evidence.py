import unittest
from types import SimpleNamespace

import orjson
from analytics_agent.agent.history import build_history
from langchain_core.messages import ToolMessage


def event(kind, payload, *, role="assistant", id="event"):
    return SimpleNamespace(event_type=kind, payload=payload, role=role, id=id)


class HistoryEvidenceTests(unittest.TestCase):
    def test_parallel_queries_restore_results_by_run_not_arrival(self):
        rows = [
            event("TEXT", {"text": "比较两组"}, role="user"),
            event(
                "TOOL_CALL",
                {"tool_name": "execute_sql", "tool_input": {"sql": "SELECT 1"}, "tool_run_id": "a"},
                id="a",
            ),
            event(
                "TOOL_CALL",
                {"tool_name": "execute_sql", "tool_input": {"sql": "SELECT 2"}, "tool_run_id": "b"},
                id="b",
            ),
            event(
                "SQL",
                {"sql": "SELECT 2", "rows": [{"v": 2}], "tool_run_id": "b", "evidence_id": "q_b"},
            ),
            event(
                "SQL",
                {"sql": "SELECT 1", "rows": [{"v": 1}], "tool_run_id": "a", "evidence_id": "q_a"},
            ),
        ]
        history = build_history(rows, "修改口径")
        results = [m for m in history if isinstance(m, ToolMessage)]
        self.assertEqual(orjson.loads(results[0].content)["evidence_id"], "q_a")
        self.assertEqual(orjson.loads(results[1].content)["rows"], [{"v": 2}])
        self.assertEqual(results[0].tool_call_id, "a")

    def test_legacy_events_still_restore(self):
        rows = [
            event("TEXT", {"text": "查询"}, role="user"),
            event(
                "TOOL_CALL",
                {"tool_name": "execute_sql", "tool_input": {"sql": "SELECT 1"}},
                id="old",
            ),
            event("SQL", {"sql": "SELECT 1", "rows": [{"v": 1}]}),
        ]
        results = [m for m in build_history(rows, "继续") if isinstance(m, ToolMessage)]
        self.assertEqual(orjson.loads(results[0].content)["rows"], [{"v": 1}])

    def test_large_result_is_valid_json_and_explicitly_incomplete(self):
        rows = [
            event("TEXT", {"text": "查询"}, role="user"),
            event("TOOL_CALL", {"tool_name": "execute_sql", "tool_input": {}}, id="old"),
            event("SQL", {"rows": [{"v": "字" * 1000}] * 100, "evidence_id": "q_large"}),
        ]
        result = next(m for m in build_history(rows, "继续") if isinstance(m, ToolMessage))
        payload = orjson.loads(result.content)
        self.assertLessEqual(len(result.content.encode()), 16000)
        self.assertEqual(len(payload["rows"]) + payload["history_rows_omitted"], 100)
        self.assertEqual(payload["evidence_id"], "q_large")
