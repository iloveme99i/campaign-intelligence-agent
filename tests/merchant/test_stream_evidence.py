import tempfile
import unittest
from pathlib import Path

from analytics_agent.agent.streaming import stream_graph_events
from analytics_agent.merchant.engine import MerchantQueryEngine
from analytics_agent.merchant.importer import import_snapshot
from analytics_agent.merchant.runtime import build_merchant_graph
from langchain_core.messages import AIMessage
from test_importer import exports
from test_runtime import ScriptedToolModel


class StreamEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_keeps_query_identity_and_snapshot_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = import_snapshot(exports(), Path(directory))
            engine = MerchantQueryEngine(Path(snapshot["path"]))
            model = ScriptedToolModel(
                messages=iter(
                    [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "execute_sql",
                                    "id": "c1",
                                    "args": {
                                        "sql": "SELECT SUM(contribution_cents) AS amount FROM order_metrics"
                                    },
                                }
                            ],
                        ),
                        AIMessage(content="测试结束"),
                    ]
                )
            )
            graph = build_merchant_graph(engine, model=model)
            events = [
                event async for event in stream_graph_events(graph, "复盘", "test", engine.name)
            ]
            self.assertFalse([e for e in events if e["event"] == "ERROR"], events)
            call = next(e["payload"] for e in events if e["event"] == "TOOL_CALL")
            result = next(e["payload"] for e in events if e["event"] == "SQL")
            self.assertEqual(result["tool_run_id"], call["tool_run_id"])
            self.assertEqual(result["snapshot_id"], snapshot["snapshot_id"])
            self.assertEqual(result["evidence_id"], engine.evidence[0]["evidence_id"])
            self.assertEqual(result["rows"], [{"amount": 3700}])
