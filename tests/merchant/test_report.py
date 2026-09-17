import unittest
from types import SimpleNamespace

from analytics_agent.merchant.report import build_report, render_markdown
from test_history_evidence import event


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.conversation = SimpleNamespace(id="c", title="复盘", engine_name="merchant_snapshot")

    def test_answer_and_evidence_are_exported_without_rewriting(self):
        evidence_id = "q_" + "a" * 32
        evidence = {"evidence_id": evidence_id, "rows": [{"amount": 3400}], "sql": "SELECT ..."}
        answer = f"商品贡献额 34 元，依据 {evidence_id}。"
        report = build_report(
            self.conversation,
            [
                event("TEXT", {"text": "活动表现"}, role="user"),
                event("SQL", evidence),
                event("COMPLETE", {"text": answer}),
            ],
        )
        turn = report["turns"][0]
        self.assertEqual(turn["answer"], answer)
        self.assertEqual(turn["evidence"], [evidence])
        self.assertEqual(turn["status"], "completed")
        self.assertEqual(turn["unresolved_evidence_ids"], [])

    def test_related_manifest_evidence_is_resolved(self):
        primary = "q_" + "a" * 32
        related = "q_" + "b" * 32
        report = build_report(
            type("Conversation", (), {"id": "c", "title": "t", "engine_name": "merchant_x"}),
            [
                event("TEXT", {"text": "复盘"}, role="user"),
                event(
                    "SQL",
                    {
                        "evidence_id": primary,
                        "related_evidence_ids": [related],
                        "evidence_manifest": [{"kind": "funnel", "evidence_id": related}],
                    },
                ),
                event("COMPLETE", {"text": f"依据 {related}"}),
            ],
        )
        self.assertEqual(report["turns"][0]["unresolved_evidence_ids"], [])

    def test_empty_complete_after_error_is_not_completed(self):
        report = build_report(
            self.conversation,
            [
                event("TEXT", {"text": "分析"}, role="user"),
                event("TEXT", {"text": "正在分析"}),
                event("ERROR", {"error": "timeout"}),
                event("COMPLETE", {"text": ""}),
            ],
        )
        self.assertEqual(report["turns"][0]["status"], "incomplete")
        self.assertEqual(report["turns"][0]["answer"], "正在分析")

    def test_unresolved_citation_is_visible(self):
        reference = "q_" + "b" * 32
        report = build_report(
            self.conversation,
            [
                event("TEXT", {"text": "分析"}, role="user"),
                event("COMPLETE", {"text": reference}),
            ],
        )
        self.assertEqual(report["turns"][0]["unresolved_evidence_ids"], [reference])

    def test_markdown_content_cannot_escape_quoted_section(self):
        text = "```\n<script>alert(1)</script>\n```"
        report = build_report(self.conversation, [event("TEXT", {"text": text}, role="user")])
        markdown = render_markdown(report)
        self.assertIn("````text\n" + text + "\n````", markdown)

    def test_correction_keeps_both_turns_without_mixing_answers(self):
        report = build_report(
            self.conversation,
            [
                event("TEXT", {"text": "第一次"}, role="user"),
                event("COMPLETE", {"text": "旧结论"}),
                event("TEXT", {"text": "修改口径"}, role="user"),
            ],
            running=True,
        )
        self.assertEqual([t["status"] for t in report["turns"]], ["completed", "running"])
        self.assertEqual(report["turns"][1]["answer"], "")

    def test_markdown_contains_readable_scope_metrics_and_experiment(self):
        evidence_id = "q_" + "c" * 32
        report = build_report(
            self.conversation,
            [
                event(
                    "TEXT",
                    {
                        "text": "复盘",
                        "scope_id": "scope-1",
                        "review_scope": {
                            "campaign_id": "summer",
                            "baseline": {"start": "2026-08-01", "end": "2026-08-07"},
                            "activity": {"start": "2026-08-08", "end": "2026-08-14"},
                            "variant": None,
                            "channel": None,
                            "location_id": None,
                            "audience": "dormant",
                            "refund_basis": "after_refunds",
                        },
                    },
                    role="user",
                ),
                event(
                    "SQL",
                    {
                        "scope_confirmed": True,
                        "evidence_id": evidence_id,
                        "rows": [
                            {
                                "period": "activity",
                                "days": 7,
                                "completed_orders": 98,
                                "buyer_users": 98,
                                "net_revenue_cents": 513000,
                                "merchant_discount_cents": 46400,
                                "contribution_cents": 258200,
                            }
                        ],
                        "experiment": {
                            "assumption": "无法验证随机分流。",
                            "comparisons": [
                                {
                                    "control": "control",
                                    "treatment": "discount_8",
                                    "difference_pp": 4.5,
                                    "ci95_difference_pp": [-0.03, 9.03],
                                    "p_value": 0.052,
                                    "sample_check": "ok",
                                }
                            ],
                        },
                    },
                ),
            ],
        )
        markdown = render_markdown(report)
        self.assertIn("### 本轮口径", markdown)
        self.assertIn("| 活动期 | 7 | 98 | 98 | ¥5,130.00", markdown)
        self.assertIn("discount_8 vs control | +4.5 个百分点", markdown)
        self.assertIn(evidence_id, markdown)

    def test_committed_decision_is_bound_and_exported(self):
        decision = {
            "status": "committed",
            "decision_outcome": "modified",
            "reason_code": "execution_constraint",
            "rationale": "预算只能支持小流量验证",
            "final_action": "先在一个分组内验证",
            "owner": "增长运营",
            "review_date": "2026-09-18",
            "note": "活动结束后复查",
            "scope_id": "scope-123",
            "answer_sha256": "a" * 64,
            "experiment_plan": {
                "baseline_rate": 0.05,
                "mde_pp": 1.0,
                "required_per_group": 8802,
                "estimated_days": 115,
                "traffic_share": 0.5,
                "decision_rule": "护栏通过后再扩大。",
            },
        }
        report = build_report(
            self.conversation,
            [
                event("TEXT", {"text": "复盘"}, role="user"),
                event("COMPLETE", {"text": "下一轮行动"}),
                event("DECISION", decision, role="user"),
                event(
                    "OUTCOME",
                    {
                        "status": "recorded",
                        "implementation_status": "completed",
                        "observed_on": "2026-09-25",
                        "next_decision": "scale",
                        "source_reference": "实验平台 EXP-0915",
                        "learning": "主指标达到门槛且护栏通过",
                        "evaluation": {
                            "status": "decision_threshold_met",
                            "control_rate": 0.05,
                            "treatment_rate": 0.065,
                            "difference_pp": 1.5,
                            "ci95_difference_pp": [0.8, 2.2],
                            "causal_readout": True,
                            "conclusion": "达到预设最小业务提升。",
                        },
                    },
                    role="user",
                ),
            ],
        )

        self.assertEqual(report["format_version"], "5")
        self.assertEqual(report["turns"][0]["decision"], decision)
        self.assertEqual(report["turns"][0]["outcome"]["next_decision"], "scale")
        markdown = render_markdown(report)
        self.assertIn("### 决策落档", markdown)
        self.assertIn("决定：修改后执行", markdown)
        self.assertIn("判断依据：预算只能支持小流量验证", markdown)
        self.assertIn("最终动作：先在一个分组内验证", markdown)
        self.assertIn("负责人：增长运营", markdown)
        self.assertIn("Scope ID：`scope-123`", markdown)
        self.assertIn("#### 已绑定实验规划", markdown)
        self.assertIn("每组样本：8,802 人", markdown)
        self.assertIn("### 执行结果回流", markdown)
        self.assertIn("后续决定：扩大执行", markdown)
        self.assertIn("差异：+1.50 个百分点", markdown)
        self.assertIn("因果判读资格：满足", markdown)

    def test_markdown_exports_tail_matching_audit_counts(self):
        report = build_report(
            self.conversation,
            [
                event("TEXT", {"text": "复盘"}, role="user"),
                event(
                    "SQL",
                    {
                        "scope_confirmed": True,
                        "attribution_days_configured": 3,
                        "attribution_tail": {
                            "completed_orders": 8,
                            "buyer_users": 7,
                            "net_revenue_cents": 56000,
                            "contribution_cents": 31000,
                            "candidate_orders": 12,
                            "orders_without_matching_exposure": 3,
                            "orders_outside_exposure_window": 1,
                        },
                    },
                ),
            ],
        )
        markdown = render_markdown(report)
        self.assertIn("同标签曝光关联订单", markdown)
        self.assertIn("候选完成订单 12 笔", markdown)
        self.assertIn("未匹配同标签曝光 3 笔", markdown)
        self.assertIn("超出曝光后归因时长 1 笔", markdown)

    def test_markdown_exports_evidence_bound_path_bottleneck(self):
        report = build_report(
            self.conversation,
            [
                event("TEXT", {"text": "复盘"}, role="user"),
                event(
                    "SQL",
                    {
                        "scope_confirmed": True,
                        "path_diagnostics": {
                            "status": "evaluated",
                            "basis": "lowest_adjacent_stage_continuation",
                            "evidence_id": "q_path1234",
                            "rows": [
                                {
                                    "variant": "阶段任务",
                                    "stage_label": "行动→完整购买",
                                    "continuation_rate": 46 / 237,
                                    "not_continued_users": 191,
                                }
                            ],
                        },
                    },
                ),
            ],
        )
        markdown = render_markdown(report)
        self.assertIn("最低承接节点", markdown)
        self.assertIn("阶段任务 | 行动→完整购买 | 19.4% | 191", markdown)
        self.assertIn("不等于可恢复增量", markdown)

    def test_markdown_omits_tail_audit_sentence_for_legacy_evidence(self):
        report = build_report(
            self.conversation,
            [
                event("TEXT", {"text": "复盘"}, role="user"),
                event(
                    "SQL",
                    {
                        "scope_confirmed": True,
                        "attribution_days_configured": 3,
                        "attribution_tail": {
                            "completed_orders": 8,
                            "buyer_users": 7,
                            "net_revenue_cents": 56000,
                            "contribution_cents": 31000,
                        },
                    },
                ),
            ],
        )
        markdown = render_markdown(report)
        self.assertIn("同标签曝光关联订单", markdown)
        self.assertNotIn("候选完成订单 — 笔", markdown)
        self.assertNotIn("未匹配同标签曝光 — 笔", markdown)

    def test_structured_review_task_is_exported(self):
        task = {
            "decision_intent": "scale",
            "risk_focus": "cost",
            "business_context": "部分门店缺货",
        }
        report = build_report(
            self.conversation,
            [
                event(
                    "TEXT",
                    {"text": "复盘", "review_task": task, "task_id": "task-123"},
                    role="user",
                )
            ],
        )
        self.assertEqual(report["turns"][0]["review_task"], task)
        markdown = render_markdown(report)
        self.assertIn("需支持的决策：能否扩大", markdown)
        self.assertIn("优先风险：成本价值", markdown)
        self.assertIn("任务标识：`task-123`", markdown)

    def test_scope_revision_is_exported_without_reusing_old_conclusion(self):
        first_scope = {
            "campaign_id": "summer",
            "baseline": {"start": "2026-08-01", "end": "2026-08-07"},
            "activity": {"start": "2026-08-08", "end": "2026-08-14"},
            "variant": None,
            "channel": None,
            "location_id": None,
            "audience": None,
            "refund_basis": "after_refunds",
        }
        second_scope = {**first_scope, "channel": "直播"}
        report = build_report(
            self.conversation,
            [
                event(
                    "TEXT",
                    {"text": "全渠道", "scope_id": "one", "review_scope": first_scope},
                    role="user",
                ),
                event(
                    "SQL",
                    {
                        "scope_confirmed": True,
                        "rows": [{"period": "activity", "completed_orders": 100}],
                    },
                ),
                event("COMPLETE", {"text": "旧结论"}),
                event(
                    "TEXT",
                    {"text": "只看直播", "scope_id": "two", "review_scope": second_scope},
                    role="user",
                ),
                event(
                    "SQL",
                    {
                        "scope_confirmed": True,
                        "rows": [{"period": "activity", "completed_orders": 80}],
                    },
                ),
                event("COMPLETE", {"text": "新结论"}),
            ],
        )
        self.assertEqual(report["scope_revision"]["status"], "revised")
        markdown = render_markdown(report)
        self.assertIn("## 口径修订审计", markdown)
        self.assertIn("| 渠道 | 全部 | 直播 |", markdown)
        self.assertIn("旧结论已失效", markdown)
