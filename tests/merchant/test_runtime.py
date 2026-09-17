"""Graph plumbing tests. Scripted model output is NOT an Agent quality evaluation."""

import tempfile
import unittest
from pathlib import Path

import orjson
from analytics_agent.merchant.engine import MerchantQueryEngine
from analytics_agent.merchant.importer import import_snapshot
from analytics_agent.merchant.runtime import MERCHANT_PROMPT, build_merchant_graph
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from test_importer import exports


class ScriptedToolModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        # GenericFakeChatModel streams content only and drops empty tool-call turns.
        # Preserve the scripted tool call when exercising astream_events.
        message = self._generate(messages, stop=stop).generations[0].message
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                tool_calls=message.tool_calls,
            )
        )


class RuntimeTests(unittest.TestCase):
    def test_prompt_requires_decision_grade_review_contract(self):
        for required in (
            "已确认事实",
            "解释假设",
            "待验证项",
            "下一轮行动",
            "护栏指标",
            "evidence_id",
        ):
            self.assertIn(required, MERCHANT_PROMPT)

    def test_upstream_graph_executes_real_merchant_tool(self):
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
                                    "id": "call_1",
                                    "args": {
                                        "sql": "SELECT SUM(contribution_cents) AS contribution FROM order_metrics"
                                    },
                                }
                            ],
                        ),
                        AIMessage(content="测试脚本结束，不作为真实模型效果。"),
                    ]
                )
            )
            graph = build_merchant_graph(engine, model=model)
            result = graph.invoke(
                {
                    "messages": [HumanMessage(content="复盘活动")],
                    "conversation_id": "test",
                    "engine_name": engine.name,
                    "user_question": "复盘活动",
                    "pending_chart": None,
                    "last_sql_result": None,
                },
                {"recursion_limit": 12},
            )
            tool_results = [m for m in result["messages"] if isinstance(m, ToolMessage)]
            self.assertEqual(len(tool_results), 1)
            self.assertEqual(
                orjson.loads(tool_results[0].content)["rows"], [{"contribution": 3700}]
            )
            self.assertEqual(len(engine.evidence), 1)
            self.assertIsNone(result["pending_chart"])
