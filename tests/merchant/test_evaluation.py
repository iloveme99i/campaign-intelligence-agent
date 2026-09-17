import unittest

from analytics_agent.merchant.evaluation import score_trace


class TraceEvaluationTests(unittest.TestCase):
    def test_internal_quality_fields_never_reach_merchant_answer(self):
        from analytics_agent.merchant.evaluation import _leaks_internal_fields

        self.assertTrue(
            _leaks_internal_fields("DiD decision_ready=true；方案差异 descriptive_signal=false。")
        )
        self.assertFalse(
            _leaks_internal_fields("DiD 识别检验通过；当前未观察到足以确认的方案差异。")
        )

    def test_raw_fen_value_never_reaches_merchant_answer(self):
        from analytics_agent.merchant.evaluation import _leaks_raw_money_units

        self.assertTrue(_leaks_raw_money_units("优惠 137,400 分即 1,374 元。"))
        self.assertFalse(_leaks_raw_money_units("优惠 1,374 元。"))

    def test_shared_cost_disclaimer_cannot_hide_scope_contradiction(self):
        from analytics_agent.merchant.evaluation import _cost_attribution_claim_is_safe

        payload = {
            "cost_boundary": {
                "status": "shared_costs_unallocated",
                "attributable_cost_cents": 0,
            }
        }
        self.assertFalse(
            _cost_attribution_claim_is_safe(
                "经营结果无法覆盖活动投入：门店贡献额 562 元低于全活动共享费用 1,288 元，尚未分摊。",
                payload,
            )
        )
        self.assertFalse(
            _cost_attribution_claim_is_safe(
                "可归属当前渠道的成本为 2,065 元；共享费用尚未分摊。",
                payload,
            )
        )
        self.assertTrue(
            _cost_attribution_claim_is_safe(
                "共享费用尚未分摊，可归属成本为 0 元；当前无法判断经营结果能否覆盖活动投入。",
                payload,
            )
        )
        self.assertTrue(
            _cost_attribution_claim_is_safe(
                "当前范围可归属成本 0.00 元，全活动共享费用 1,666.00 元尚未分摊，"
                "因此当前无法判断经营结果能否覆盖活动投入。",
                payload,
            )
        )

    def test_scoped_daily_subtotal_must_match_exact_timeline_slice(self):
        from analytics_agent.merchant.evaluation import _money_values_are_grounded

        values = [212, 268, 236, 108, 132, 136, 240]
        payload = {
            "rows": [{"period": "activity", "contribution_cents": 133200}],
            "timeline_bin_days": 1,
            "timeline": [
                {"period": "activity", "bin_index": index, "contribution_cents": value * 100}
                for index, value in enumerate(values)
            ],
        }
        self.assertTrue(
            _money_values_are_grounded("活动期前 3 天贡献额 716 元，后 4 天 616 元。", payload)
        )
        self.assertFalse(_money_values_are_grounded("活动期前 3 天贡献额 717 元。", payload))
        self.assertFalse(_money_values_are_grounded("贡献额 716 元。", payload))

    def test_stage_rate_cannot_borrow_purchase_per_exposure_test_statistics(self):
        from analytics_agent.merchant.evaluation import (
            _rate_differences_match_displayed_rates,
            _statistical_metric_lineage_is_clear,
        )

        mistaken = (
            "阶段任务激活到路径购买率 19.41%（46/237），登录礼包 9.71%（20/206），"
            "差值 4.81 个百分点，95% 区间 1.97–7.66 个百分点，p≈0.00096。"
        )
        careful = (
            "激活到路径购买：阶段任务 19.41%，登录礼包 9.71%，描述性差值 9.70 个百分点。\n"
            "购买/曝光：阶段任务 8.52%，登录礼包 3.70%，差值 4.82 个百分点，"
            "95% 区间 1.97–7.66 个百分点，p≈0.00096。"
        )
        self.assertFalse(_rate_differences_match_displayed_rates(mistaken))
        self.assertFalse(_statistical_metric_lineage_is_clear(mistaken))
        self.assertTrue(_rate_differences_match_displayed_rates(careful))
        self.assertTrue(_statistical_metric_lineage_is_clear(careful))

    def test_inferential_lineage_is_scoped_to_the_claim_clause(self):
        from analytics_agent.merchant.evaluation import _statistical_metric_lineage_is_clear

        answer = (
            "最低承接节点为完成关键行动→完整路径购买。"
            "购买/曝光率为 5.00% 与 7.78%，差值 2.78 个百分点，"
            "95% 区间 -2.27 至 7.82 个百分点，p=0.28。"
        )

        self.assertTrue(_statistical_metric_lineage_is_clear(answer))

    def test_summary_difference_can_reuse_later_expanded_evidence(self):
        from analytics_agent.merchant.evaluation import _rate_differences_match_displayed_rates

        answer = (
            "总盘：购买/曝光差值 2.78 个百分点。\n"
            "证据：购买/曝光率为 5.00% 与 7.78%，差值 2.78 个百分点。"
        )

        self.assertTrue(_rate_differences_match_displayed_rates(answer))

    def test_summary_difference_ignores_rates_from_an_earlier_sentence(self):
        from analytics_agent.merchant.evaluation import _rate_differences_match_displayed_rates

        answer = (
            "路径承接率为 13.24% 与 18.42%。购买/曝光差值 2.78 个百分点。\n"
            "购买/曝光率为 5.00% 与 7.78%，差值 2.78 个百分点。"
        )

        self.assertTrue(_rate_differences_match_displayed_rates(answer))

    def test_subset_experiment_needs_control_inside_the_same_cohort(self):
        from analytics_agent.merchant.evaluation import _control_stays_in_randomized_cohort

        variants = ["登录礼包", "阶段任务"]
        self.assertFalse(
            _control_stays_in_randomized_cohort(
                "- 改变对象：仅对阶段任务组用户做用户级随机分流，登录礼包组保留为对照。",
                variants,
            )
        )
        self.assertTrue(
            _control_stays_in_randomized_cohort(
                "- 改变对象：仅对阶段任务组用户随机分流；本组未加新触点者为对照，登录礼包只作观察参照。",
                variants,
            )
        )

    def test_negated_variant_allocation_is_not_mistaken_for_direct_shift(self):
        from analytics_agent.merchant.evaluation import _unqualified_variant_allocation

        self.assertFalse(
            _unqualified_variant_allocation("当前不支持把预算、曝光或入口位向任一方案倾斜。")
        )
        self.assertFalse(_unqualified_variant_allocation("不能据此把预算转向任一门店。"))
        self.assertTrue(_unqualified_variant_allocation("下一轮将预算向 A 方案倾斜。"))

    def test_structured_answer_must_resolve_requested_decision_intent(self):
        from analytics_agent.merchant.evaluation import _covers_decision_intent

        self.assertTrue(
            _covers_decision_intent(
                "总盘结论：当前不支持扩大覆盖。\n下一轮行动：若主指标区间为正再放量。",
                {"decision_intent": "scale"},
            )
        )
        self.assertFalse(
            _covers_decision_intent(
                "总盘结论：目标未达成，贡献额下降。\n下一轮行动：继续观察。",
                {"decision_intent": "stop"},
            )
        )

    def test_next_experiment_requires_one_executable_primary_metric(self):
        from analytics_agent.merchant.evaluation import _single_executable_primary_metric

        self.assertFalse(
            _single_executable_primary_metric(
                "- 验证指标：主指标用购买用户/曝光用户（或完成关键行动用户/领取权益用户）。"
            )
        )
        self.assertTrue(
            _single_executable_primary_metric("- 验证指标：完整路径购买用户/激活用户。")
        )
        self.assertTrue(
            _single_executable_primary_metric(
                "- 验证指标：主指标用购买用户/曝光用户；路径后购买仅作诊断。"
            )
        )

    def test_discount_daily_yuan_amount_cannot_exceed_activity_total(self):
        from analytics_agent.merchant.evaluation import _discount_amount_sanity

        payload = {
            "rows": [
                {
                    "period": "activity",
                    "merchant_discount_cents": 56200,
                }
            ]
        }
        self.assertFalse(
            _discount_amount_sanity(
                "假设：活动期商家承担优惠从 4,000–7,800 元/日升至 13,800–14,400 元/日。",
                payload,
            )
        )
        self.assertTrue(
            _discount_amount_sanity(
                "事实：商家优惠 562 元；8 月 11 日优惠 144 元。",
                payload,
            )
        )

    def test_raw_cents_cannot_be_labeled_as_yuan(self):
        from analytics_agent.merchant.evaluation import _money_unit_scale_sanity

        payload = {
            "rows": [
                {
                    "period": "baseline",
                    "net_revenue_cents": 356700,
                    "contribution_cents": 258150,
                },
                {
                    "period": "activity",
                    "net_revenue_cents": 326900,
                    "contribution_cents": 229600,
                },
            ],
            "costs": [
                {"activity_cost_cents": 70000},
                {"activity_cost_cents": 96600},
            ],
        }
        self.assertFalse(
            _money_unit_scale_sanity(
                "经营贡献由 258,150 元降至 229,600 元。",
                payload,
            )
        )
        self.assertFalse(
            _money_unit_scale_sanity(
                "净收入由 356,700 元降至 326,900 元。",
                payload,
            )
        )
        self.assertTrue(
            _money_unit_scale_sanity(
                "经营贡献由 2,581.50 元降至 2,296 元，减少 285.50 元；"
                "净收入由 3,567 元降至 3,269 元；总投入 1,666 元。",
                payload,
            )
        )

    def test_monetary_target_is_not_mistaken_for_raw_cents(self):
        from analytics_agent.merchant.evaluation import _money_unit_scale_sanity

        payload = {
            "rows": [
                {"period": "baseline", "net_revenue_cents": 228900},
                {"period": "activity", "net_revenue_cents": 110900},
            ],
            "target": {
                "metric": "net_revenue",
                "unit": "cents",
                "actual_value": 110900,
                "target_value": 620000,
                "gap": -509100,
            },
        }

        self.assertTrue(
            _money_unit_scale_sanity(
                "活动期净收入 1,109 元，目标 6,200 元，缺口 5,091 元。",
                payload,
            )
        )

    def test_metric_labelled_money_must_match_deterministic_values(self):
        from analytics_agent.merchant.evaluation import _money_values_are_grounded

        payload = {
            "rows": [
                {
                    "period": "baseline",
                    "net_revenue_cents": 356700,
                    "contribution_cents": 258150,
                    "merchant_discount_cents": 0,
                },
                {
                    "period": "activity",
                    "net_revenue_cents": 326900,
                    "contribution_cents": 229600,
                    "merchant_discount_cents": 26700,
                },
            ],
            "timeline": [
                {"period": "baseline", "contribution_cents": 35500},
                {"period": "activity", "contribution_cents": 24000},
            ],
            "segments": {
                "channel": [{"dimension_value": "wechat", "order_contribution_cents": 67200}],
            },
            "costs": [
                {"activity_cost_cents": 70000},
                {"activity_cost_cents": 96600},
            ],
        }
        correct = (
            "贡献额由 2,581.50 元降至 2,296 元，减少 285.50 元；"
            "净收入由 3,567 元降至 3,269 元；商家优惠 267 元；"
            "活动成本合计 1,666 元，其中 966 元与 700 元。"
        )
        self.assertTrue(_money_values_are_grounded(correct, payload))
        self.assertFalse(
            _money_values_are_grounded(
                correct.replace("2,296 元", "2,396 元"),
                payload,
            )
        )
        self.assertTrue(
            _money_values_are_grounded(
                "逐日贡献额低点为 240 元，对比期读数 355 元；渠道贡献额为 672 元。",
                payload,
            )
        )

    def test_conjunction_linked_preposed_cost_is_not_misread_as_discount(self):
        from analytics_agent.merchant.evaluation import (
            _discount_amount_sanity,
            _money_unit_scale_sanity,
            _money_values_are_grounded,
        )

        payload = {
            "rows": [
                {"period": "baseline", "merchant_discount_cents": 0},
                {"period": "activity", "merchant_discount_cents": 137400},
            ],
            "costs": [
                {"activity_cost_cents": 94500},
                {"activity_cost_cents": 112000},
            ],
        }
        answer = "商家优惠从 0 元增至 1,374.00 元与 2,065.00 元活动成本的叠加。"

        self.assertTrue(_discount_amount_sanity(answer, payload))
        self.assertTrue(_money_unit_scale_sanity(answer, payload))
        self.assertTrue(_money_values_are_grounded(answer, payload))

    def test_contribution_after_attributable_cost_is_a_grounded_relationship(self):
        from analytics_agent.merchant.evaluation import _money_values_are_grounded

        payload = {
            "rows": [{"period": "activity", "contribution_cents": 219100}],
            "costs": [
                {"activity_cost_cents": 78400},
                {"activity_cost_cents": 50400},
            ],
        }

        self.assertTrue(
            _money_values_are_grounded(
                "活动期贡献额 2,191 元，扣除已归属活动成本后剩余 903 元的账面贡献。",
                payload,
            )
        )
        self.assertFalse(_money_values_are_grounded("活动成本 903 元。", payload))

    def test_shared_bottleneck_can_name_two_variants_across_a_semicolon(self):
        from analytics_agent.merchant.evaluation import _path_bottleneck_claim_is_grounded

        diagnostic = {
            "rows": [
                {"variant": "登录礼包", "stage_key": "activation_to_path_purchase"},
                {"variant": "阶段任务", "stage_key": "activation_to_path_purchase"},
            ]
        }
        answer = "最低承接节点：登录礼包‘行动→完整购买’承接率 9.71%；阶段任务 19.41%。"
        self.assertTrue(_path_bottleneck_claim_is_grounded(answer, diagnostic))

    def test_shared_bottleneck_allows_a_stage_less_evidence_expansion(self):
        from analytics_agent.merchant.evaluation import _path_bottleneck_claim_is_grounded

        diagnostic = {
            "rows": [
                {"variant": "登录礼包", "stage_key": "activation_to_path_purchase"},
                {"variant": "阶段任务", "stage_key": "activation_to_path_purchase"},
            ]
        }
        answer = (
            "用户路径最低承接节点为‘行动→完整购买’：登录礼包 68→9，13.24%；"
            "阶段任务 76→14，18.42%。\n"
            "实验组最低承接节点：登录礼包 13.24%、阶段任务 18.42%，两组节点相同。"
        )
        self.assertTrue(_path_bottleneck_claim_is_grounded(answer, diagnostic))
        self.assertFalse(
            _path_bottleneck_claim_is_grounded(
                "实验组最低承接节点：登录礼包 13.24%、阶段任务 18.42%。",
                diagnostic,
            )
        )

    def test_primary_metric_cannot_claim_to_match_a_different_bottleneck(self):
        from analytics_agent.merchant.evaluation import _path_bottleneck_claim_is_grounded

        diagnostic = {
            "rows": [
                {"variant": "A", "stage_key": "activation_to_path_purchase"},
                {"variant": "B", "stage_key": "activation_to_path_purchase"},
            ]
        }
        mismatched = (
            "最低承接节点：A 与 B 均为行动→完整购买。\n"
            "- 验证指标：激活用户/权益领取用户，对应最低承接节点行动→完整购买。"
        )
        aligned = mismatched.replace(
            "激活用户/权益领取用户",
            "完整路径购买用户/激活用户",
        )

        self.assertFalse(_path_bottleneck_claim_is_grounded(mismatched, diagnostic))
        self.assertTrue(_path_bottleneck_claim_is_grounded(aligned, diagnostic))

    def test_missing_calendar_rows_require_export_caveat(self):
        answer = (
            "总盘结论\n目标结果已核对。\n已确认事实\n- 路径与成本已核对（q_12345678）。\n"
            "解释假设\n可能有执行差异。\n待验证项\n随机分流尚未验证。\n"
            "下一轮行动\n改变对象为门店；验证指标为转化率，护栏指标为贡献额，符合条件才继续。"
        )
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "evidence_id": "q_12345678",
                    "data_coverage": {
                        "baseline_days": 7,
                        "baseline_days_with_orders": 7,
                        "activity_days": 7,
                        "activity_days_with_orders": 6,
                        "activity_days_with_exposure": 7,
                    },
                },
            },
            {"event_type": "COMPLETE", "role": "assistant", "payload": {"text": answer}},
        ]
        self.assertFalse(score_trace(messages).checks["decision_coverage"])
        messages[-1]["payload"]["text"] = answer.replace(
            "待验证项\n随机分流尚未验证。",
            "待验证项\n随机分流尚未验证；订单导出有无记录日，需核对是否漏导。",
        )
        self.assertTrue(score_trace(messages).checks["decision_coverage"])

    def test_numbered_conclusion_heading_is_not_an_uncited_numeric_fact(self):
        from analytics_agent.merchant.evaluation import _numeric_conclusion_lines_are_cited

        answer = (
            "**1. 总盘结论**\n\n"
            "- 活动期 71 单，较对比期减少 23 单（q_12345678）。\n"
            "**2. 已确认事实**\n- 已核对。"
        )
        self.assertTrue(_numeric_conclusion_lines_are_cited(answer))

    def test_observational_variant_does_not_justify_direct_resource_shift(self):
        answer = (
            "总盘结论\n目标未达成。\n已确认事实\n- 路径差异已核对。q_12345678\n"
            "解释假设\n方案结构可能不同。\n待验证项\n随机分流未验证。\n"
            "下一轮行动\n改变对象：将入口曝光向阶段任务迁移。\n"
            "验证指标：转化率；护栏指标：贡献额；不达标则停止。"
        )
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "diagnostic_dimension": "variant",
                    "scope_id": "scope-a",
                    "experiment": {"status": "observational_readout"},
                },
            },
            {"event_type": "COMPLETE", "role": "assistant", "payload": {"text": answer}},
        ]

        self.assertFalse(score_trace(messages).checks["variant_allocation_boundary"])
        messages[-1]["payload"]["text"] = answer.replace(
            "改变对象：将入口曝光向阶段任务迁移。",
            "改变对象：在保留对照的随机分流试点中，将少量入口曝光向阶段任务迁移，先验证差异。",
        )
        self.assertTrue(score_trace(messages).checks["variant_allocation_boundary"])

    def test_shared_campaign_cost_requires_an_attribution_boundary(self):
        answer = (
            "目标结果已核对。\n已确认事实\n"
            "- 转化率 12.5%，路径与成本已核对。q_12345678\n"
            "解释假设\n优惠可能影响行为。\n待验证项\n随机分流。\n"
            "下一轮行动\n对象为推送渠道；验证指标为转化率，护栏指标为全活动成本，"
            "达到条件继续，否则停止。前后相关变化不能证明活动因果。"
        )
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                    "cost_boundary": {"status": "shared_costs_unallocated"},
                },
            },
            {"event_type": "COMPLETE", "role": "assistant", "payload": {"text": answer}},
        ]

        result = score_trace(messages)
        self.assertFalse(result.checks["cost_attribution_boundary"])
        bounded = answer.replace(
            "待验证项\n随机分流。",
            "待验证项\n随机分流；全活动共享费用尚未分摊，不能归属到推送渠道。",
        )
        messages[-1]["payload"]["text"] = bounded
        self.assertTrue(score_trace(messages).checks["cost_attribution_boundary"])

    def test_complete_answer_supersedes_streamed_token_chunks(self):
        answer = (
            "目标达成但贡献下降。\n已确认事实\n"
            "- 转化率 12.5%，路径与成本已核对。q_12345678\n"
            "解释假设\n优惠可能影响行为。\n待验证项\n随机分流。\n"
            "下一轮行动\n对象为优惠实验组；验证指标为转化率，护栏指标为贡献额，"
            "达到条件继续，否则停止。前后相关变化不能证明活动因果。"
        )
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                    "target": {"achieved": True},
                    "rows": [
                        {"period": "baseline", "contribution_cents": 20000},
                        {"period": "activity", "contribution_cents": 15000},
                    ],
                },
            },
            {"event_type": "TEXT", "role": "assistant", "payload": {"text": "目"}},
            {"event_type": "TEXT", "role": "assistant", "payload": {"text": "标"}},
            {"event_type": "COMPLETE", "role": "assistant", "payload": {"text": answer}},
        ]

        result = score_trace(messages)

        self.assertTrue(result.passed, result.checks)

    def test_decision_commit_does_not_hide_the_turn_from_trace_scoring(self):
        answer = (
            "目标达成但贡献下降。\n已确认事实\n"
            "- 转化率 12.5%，路径与成本已核对。q_12345678\n"
            "解释假设\n优惠可能影响行为。\n待验证项\n随机分流。\n"
            "下一轮行动\n对象为优惠实验组；验证指标为转化率，护栏指标为贡献额，"
            "达到条件继续，否则停止。前后相关变化不能证明活动因果。"
        )
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                    "target": {"achieved": True},
                    "rows": [
                        {"period": "baseline", "contribution_cents": 20000},
                        {"period": "activity", "contribution_cents": 15000},
                    ],
                },
            },
            {"event_type": "COMPLETE", "role": "assistant", "payload": {"text": answer}},
            {"event_type": "DECISION", "role": "user", "payload": {"status": "committed"}},
        ]

        result = score_trace(messages)

        self.assertTrue(result.passed, result.checks)

    def test_evidence_grounded_answer_passes(self):
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                    "related_evidence_ids": [],
                },
            },
            {
                "event_type": "TEXT",
                "role": "assistant",
                "payload": {
                    "text": "目标结果已核对。\n已确认事实\n- 转化率 12.5%，转化路径发生变化，活动成本需要控制。q_12345678\n解释假设\n优惠可能影响行为。\n待验证项\n随机分流。\n下一轮行动\n对象为优惠实验组；验证指标为转化率，护栏指标为贡献额，达到预设条件继续，否则停止。前后相关变化不能证明活动因果。"
                },
            },
        ]
        result = score_trace(messages)
        self.assertTrue(result.passed, result.checks)
        self.assertEqual(result.score, 1.0)

    def test_all_core_evidence_must_be_cited(self):
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                    "related_evidence_ids": ["q_87654321"],
                    "evidence_manifest": [
                        {"kind": "outcome", "evidence_id": "q_12345678"},
                        {"kind": "funnel", "evidence_id": "q_87654321"},
                    ],
                },
            },
            {
                "event_type": "TEXT",
                "role": "assistant",
                "payload": {
                    "text": "目标结果已核对。\n已确认事实\n- 转化率 12.5%。q_12345678\n解释假设\n优惠可能影响路径。\n待验证项\n随机性。\n下一轮行动\n对象为实验组；验证指标为转化率，护栏指标为成本，达到条件继续，否则停止。前后相关变化不能证明活动因果。"
                },
            },
        ]
        result = score_trace(messages)
        self.assertFalse(result.checks["core_evidence_covered"])

    def test_numeric_fact_without_same_line_evidence_fails(self):
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "TEXT",
                "role": "assistant",
                "payload": {
                    "text": "目标结果已核对。\n已确认事实\n- 转化率 12.5%。\n证据 q_12345678\n解释假设\n优惠可能影响路径。\n待验证项\n随机性。\n下一轮行动\n对象为实验组；验证指标为转化率，护栏指标为成本，达到条件继续，否则停止。前后相关变化不能证明活动因果。"
                },
            },
        ]
        result = score_trace(messages)
        self.assertFalse(result.checks["numeric_facts_cited"])

    def test_numeric_conclusion_without_evidence_fails(self):
        messages = [
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": "目标达成，转化率 12.5%。\n已确认事实\n"
                    "- 转化率 12.5%。q_12345678\n解释假设\n优惠可能影响路径。\n"
                    "待验证项\n随机性。\n下一轮行动\n对象为实验组；验证指标为转化率，"
                    "护栏指标为成本，达到条件继续，否则停止。前后变化不能证明活动因果。"
                },
            },
        ]

        result = score_trace(messages)

        self.assertFalse(result.checks["numeric_conclusion_cited"])

    def test_scope_metadata_in_fact_heading_does_not_need_evidence(self):
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": "目标结果已核对。\n已确认事实（活动 2026-08-08 至 2026-08-14）\n"
                    "- 转化率 12.5%，路径与成本已核对。q_12345678\n"
                    "解释假设\n优惠可能影响路径。\n待验证项\n随机性。\n下一轮行动\n"
                    "对象为实验组；验证指标为转化率，护栏指标为成本，达到条件继续，否则停止。"
                    "前后相关变化不能证明活动因果。"
                },
            },
        ]

        result = score_trace(messages)

        self.assertTrue(result.checks["numeric_facts_cited"])

    def test_target_cost_tension_is_not_optional(self):
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                    "target": {"achieved": True},
                    "rows": [
                        {"period": "baseline", "contribution_cents": 20000},
                        {"period": "activity", "contribution_cents": 15000},
                    ],
                },
            },
            {
                "event_type": "TEXT",
                "role": "assistant",
                "payload": {
                    "text": "目标结果已核对。\n已确认事实\n- 转化率 12.5%。q_12345678\n解释假设\n优惠可能影响路径。\n待验证项\n随机性。\n下一轮行动\n对象为实验组；验证指标为转化率，护栏指标为成本，达到条件继续，否则停止。前后相关变化不能证明活动因果。"
                },
            },
        ]
        result = score_trace(messages)
        self.assertFalse(result.checks["target_cost_tension"])

    def test_fabricated_evidence_and_missing_boundary_fail(self):
        messages = [
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "TEXT",
                "role": "assistant",
                "payload": {
                    "text": "目标结果、转化路径和活动成本都很好，下一轮建议扩大。证据 q_deadbeef。"
                },
            },
        ]
        result = score_trace(messages)
        self.assertFalse(result.checks["evidence_cited"])
        self.assertFalse(result.checks["causal_boundary"])

    def test_explicit_not_causal_effect_wording_passes_boundary(self):
        messages = [
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": "已确认事实\n- 转化率 12.5%。q_12345678\n解释假设\n优惠影响路径。"
                    "\n待验证项\n组间差异不能作为活动因果效果。\n下一轮行动\n"
                    "对象为实验组；验证指标为转化率，护栏指标为成本，达到条件继续，否则停止。"
                    "目标结果、转化路径和活动成本已核对。"
                },
            },
        ]

        result = score_trace(messages)

        self.assertTrue(result.checks["causal_boundary"])

    def test_causal_disclaimer_does_not_excuse_a_causal_action_claim(self):
        messages = [
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": "目标达成。\n已确认事实\n- 路径与成本已核对。q_12345678\n"
                    "解释假设\n优惠可能影响路径。\n待验证项\n不能把前后差异当作活动因果效果。\n"
                    "下一轮行动\n改变对象为折扣实验组；优惠未带来订单增量，验证指标为转化率，"
                    "护栏指标为成本，达到条件继续，否则停止。"
                },
            },
        ]

        result = score_trace(messages)

        self.assertTrue(result.checks["causal_boundary"])
        self.assertFalse(result.checks["no_unsupported_causal_claim"])

    def test_validated_did_allows_conditional_causal_claim_with_method_boundary(self):
        messages = [
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                    "incrementality": {
                        "status": "identified",
                        "estimates": [
                            {
                                "metric": "completed_orders",
                                "decision_ready": True,
                            }
                        ],
                    },
                },
            },
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": (
                        "总盘结论\n平衡面板 DiD 估计活动带来订单增量；"
                        "该 ATT 是每个处理经营单元的活动后周期均值，"
                        "相对活动前均值的平均效应，仅在同期对照、稳定构成与无干扰假设下成立。q_12345678\n"
                        "已确认事实\n- 完成订单增量为 2。q_12345678\n"
                        "解释假设\n无干扰假设已审查。\n待验证项\n外推范围。\n"
                        "下一轮行动\n改变对象为实验组；验证指标为购买用户/曝光用户；"
                        "护栏指标为贡献额；达到条件继续，否则停止。"
                    )
                },
            },
        ]

        result = score_trace(messages)

        self.assertTrue(result.checks["causal_boundary"])
        self.assertTrue(result.checks["incrementality_estimand_clarity"])
        self.assertTrue(result.checks["no_unsupported_causal_claim"])

    def test_validated_did_without_unit_and_period_estimand_fails_clarity_gate(self):
        messages = [
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                    "incrementality": {
                        "status": "identified",
                        "estimates": [{"metric": "completed_orders", "decision_ready": True}],
                    },
                },
            },
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": (
                        "总盘结论\n平衡面板 DiD 估计 ATT 为 2，"
                        "仅在同期对照、稳定构成与无干扰假设下成立。q_12345678\n"
                        "已确认事实\n- 完成订单增量为 2。q_12345678\n"
                        "解释假设\n无干扰假设已审查。\n待验证项\n外推范围。\n"
                        "下一轮行动\n改变对象为实验组；验证指标为购买用户/曝光用户；"
                        "护栏指标为贡献额；达到条件继续，否则停止。"
                    )
                },
            },
        ]

        result = score_trace(messages)

        self.assertTrue(result.checks["causal_boundary"])
        self.assertFalse(result.checks["incrementality_estimand_clarity"])

    def test_low_power_pretrend_must_be_disclosed(self):
        payload = {
            "scope_confirmed": True,
            "scope_id": "scope-a",
            "truncated": False,
            "evidence_id": "q_12345678",
            "incrementality": {
                "status": "identified",
                "estimates": [
                    {
                        "metric": "completed_orders",
                        "decision_ready": True,
                        "pretrend_power": "low",
                    }
                ],
            },
        }
        base = (
            "总盘结论\n平衡面板 DiD 估计活动增量；该 ATT 是每个处理经营单元"
            "的活动后周期均值，相对活动前均值的平均效应，仅在同期对照、"
            "稳定构成与无干扰假设下成立。q_12345678\n"
            "已确认事实\n- 完成订单增量为 2。q_12345678\n"
            "解释假设\n无干扰。\n待验证项\n外推范围。\n"
            "下一轮行动\n改变对象为实验组；验证指标为购买用户/曝光用户；"
            "护栏指标为贡献额；达到条件继续，否则停止。"
        )
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
            {"event_type": "SQL", "role": "assistant", "payload": payload},
            {"event_type": "COMPLETE", "role": "assistant", "payload": {"text": base}},
        ]
        self.assertFalse(score_trace(messages).checks["incrementality_pretrend_power_clarity"])
        messages[-1]["payload"]["text"] = (
            base + "\n仅3个前期，前趋势检验力有限，未拒绝不等于证明平行趋势。"
        )
        self.assertTrue(score_trace(messages).checks["incrementality_pretrend_power_clarity"])

    def test_unqualified_future_effect_claim_fails(self):
        messages = [
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": "扩量会进一步压缩贡献。\n已确认事实\n- 路径与成本已核对。q_12345678\n"
                    "解释假设\n优惠可能影响路径。\n待验证项\n不能把前后差异当作活动因果效果。\n"
                    "下一轮行动\n改变对象为实验组；验证指标为转化率，护栏指标为成本，"
                    "达到条件继续，否则停止。"
                },
            },
        ]

        result = score_trace(messages)

        self.assertFalse(result.checks["no_unqualified_forecast"])

    def test_task_conclusion_must_use_diagnostic_evidence(self):
        messages = [
            {
                "event_type": "TEXT",
                "role": "user",
                "payload": {
                    "text": "能否扩量",
                    "review_task": {
                        "decision_intent": "scale",
                        "risk_focus": "variant",
                        "business_context": "",
                    },
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "TOOL_CALL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "tool_input": {"dimension": "variant", "reason": "判断扩量条件"},
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_87654321",
                },
            },
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": "当前证据不支持扩量。q_12345678\n已确认事实\n"
                    "- 转化路径与活动成本已核对。q_12345678\n解释假设\n优惠可能影响路径。\n"
                    "待验证项\n前后相关变化不能证明活动因果。\n下一轮行动\n"
                    "改变对象为实验组；验证指标为转化率，护栏指标为成本，达到条件继续，否则停止。"
                },
            },
        ]

        result = score_trace(messages)

        self.assertFalse(result.checks["diagnosis_supports_conclusion"])

    def test_latest_turn_does_not_mix_an_older_scope(self):
        messages = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "旧口径"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-old",
                    "truncated": False,
                    "evidence_id": "q_11111111",
                },
            },
            {"event_type": "TEXT", "role": "user", "payload": {"text": "新口径"}},
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-new",
                    "truncated": False,
                    "evidence_id": "q_22222222",
                },
            },
        ]
        result = score_trace(messages, require_answer=False)
        self.assertTrue(result.checks["single_scope"])
        self.assertEqual(result.evidence_ids, ("q_22222222",))

    def test_model_free_pipeline_can_score_structural_gates(self):
        messages = [
            {
                "event_type": "TOOL_CALL",
                "role": "assistant",
                "payload": {"tool_name": "compare_periods"},
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {"event_type": "ERROR", "role": "assistant", "payload": {"error": "模型未配置"}},
        ]
        result = score_trace(messages, require_answer=False)
        self.assertTrue(result.passed, result.checks)

    def test_structured_task_requires_one_reasoned_matching_diagnosis(self):
        messages = [
            {
                "event_type": "TEXT",
                "role": "user",
                "payload": {
                    "text": "判断是否调整",
                    "review_task": {
                        "decision_intent": "adjust",
                        "risk_focus": "cost",
                        "business_context": "预算不增加",
                    },
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "TOOL_CALL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "tool_input": {
                        "dimension": "variant",
                        "reason": "比较实验组的成本效率",
                    },
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_87654321",
                },
            },
        ]

        result = score_trace(messages, require_answer=False)

        self.assertTrue(result.passed, result.checks)

    def test_structured_task_rejects_unexplained_or_overbroad_diagnosis(self):
        messages = [
            {
                "event_type": "TEXT",
                "role": "user",
                "payload": {
                    "text": "判断是否调整",
                    "review_task": {
                        "decision_intent": "adjust",
                        "risk_focus": "cost",
                        "business_context": "",
                    },
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            *[
                {
                    "event_type": "TOOL_CALL",
                    "role": "assistant",
                    "payload": {
                        "tool_name": "diagnose_dimension",
                        "tool_input": {"dimension": dimension, "reason": reason},
                    },
                }
                for dimension, reason in (("channel", ""), ("audience", "继续看看"))
            ],
        ]

        result = score_trace(messages, require_answer=False)

        self.assertFalse(result.checks["diagnostic_reason_recorded"])
        self.assertTrue(result.checks["diagnostic_selective"])
        self.assertFalse(result.checks["diagnosis_matches_task"])

        duplicate = list(messages)
        duplicate.append(
            {
                "event_type": "TOOL_CALL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "tool_input": {
                        "dimension": "channel",
                        "reason": "重复复核渠道",
                    },
                },
            }
        )
        self.assertFalse(
            score_trace(duplicate, require_answer=False).checks["diagnostic_selective"]
        )

    def test_structured_task_rejects_diagnosis_from_another_scope(self):
        messages = [
            {
                "event_type": "TEXT",
                "role": "user",
                "payload": {
                    "text": "判断是否调整",
                    "review_task": {
                        "decision_intent": "adjust",
                        "risk_focus": "variant",
                        "business_context": "",
                    },
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "TOOL_CALL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "tool_input": {"dimension": "variant", "reason": "核对实验组"},
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "scope_id": "scope-b",
                    "truncated": False,
                    "evidence_id": "q_87654321",
                },
            },
        ]

        result = score_trace(messages, require_answer=False)

        self.assertFalse(result.checks["diagnostic_scope_consistent"])

    def test_cost_task_accepts_channel_when_user_explicitly_requests_it(self):
        messages = [
            {
                "event_type": "TEXT",
                "role": "user",
                "payload": {
                    "text": "核对渠道成本与路径，再判断是否扩量",
                    "review_task": {
                        "decision_intent": "scale",
                        "risk_focus": "cost",
                        "business_context": "预算不增加",
                    },
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "TOOL_CALL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "tool_input": {"dimension": "channel", "reason": "核对渠道投入与承接"},
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_87654321",
                },
            },
        ]

        result = score_trace(messages, require_answer=False)
        self.assertTrue(result.checks["diagnosis_matches_task"])

    def test_cost_task_accepts_relevant_channel_and_location_diagnosis(self):
        messages = [
            {
                "event_type": "TEXT",
                "role": "user",
                "payload": {
                    "text": "判断经营结果能否覆盖活动投入",
                    "review_task": {
                        "decision_intent": "adjust",
                        "risk_focus": "cost",
                        "business_context": "",
                    },
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            *[
                {
                    "event_type": "TOOL_CALL",
                    "role": "assistant",
                    "payload": {
                        "tool_name": "diagnose_dimension",
                        "tool_input": {"dimension": dimension, "reason": reason},
                    },
                }
                for dimension, reason in (
                    ("channel", "核对渠道投入与承接"),
                    ("location_id", "核对经营点贡献差异"),
                )
            ],
        ]

        result = score_trace(messages, require_answer=False)

        self.assertTrue(result.checks["diagnosis_matches_task"])

    def test_cost_task_can_follow_an_explicit_stockout_constraint(self):
        messages = [
            {
                "event_type": "TEXT",
                "role": "user",
                "payload": {
                    "text": "预算不增加，门店有缺货",
                    "review_task": {
                        "decision_intent": "adjust",
                        "risk_focus": "cost",
                        "business_context": "部分经营点活动期缺货",
                    },
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_12345678",
                },
            },
            {
                "event_type": "TOOL_CALL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "tool_input": {
                        "dimension": "location_id",
                        "reason": "定位缺货经营点的路径与贡献损失",
                    },
                },
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "scope_id": "scope-a",
                    "truncated": False,
                    "evidence_id": "q_87654321",
                },
            },
        ]

        result = score_trace(messages, require_answer=False)

        self.assertTrue(result.checks["diagnosis_matches_task"])

    def test_equal_total_and_path_buyers_does_not_erase_activation_purchase_gap(self):
        base = [
            {
                "event_type": "TEXT",
                "role": "user",
                "payload": {"text": "检查任务完成到购买是否脱节"},
            },
            {
                "event_type": "SQL",
                "role": "assistant",
                "payload": {
                    "scope_confirmed": True,
                    "scope_id": "scope-a",
                    "metric_version": "v0.8",
                    "evidence_id": "q_12345678",
                    "funnel": [
                        {
                            "variant": "登录礼包",
                            "activated_users": 206,
                            "path_buyer_users": 20,
                            "buyer_users": 20,
                        },
                        {
                            "variant": "阶段任务",
                            "activated_users": 237,
                            "path_buyer_users": 46,
                            "buyer_users": 46,
                        },
                    ],
                },
            },
        ]
        mistaken = base + [
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": "路径购买数等于全部购买数，未观察到该组内任务完成与购买之间的断点。"
                },
            }
        ]
        careful = base + [
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": "路径购买数等于全部购买数，不意味着任务完成到购买没有流失；阶段任务 237 名行动用户中仅 46 名沿完整路径购买。"
                },
            }
        ]

        self.assertFalse(score_trace(mistaken).checks["path_stage_consistency"])
        self.assertTrue(score_trace(careful).checks["path_stage_consistency"])

    def test_path_bottleneck_claim_must_match_deterministic_stage(self):
        payload = {
            "scope_confirmed": True,
            "scope_id": "scope-a",
            "metric_version": "v0.8",
            "evidence_id": "q_12345678",
            "path_diagnostics": {
                "status": "evaluated",
                "rows": [
                    {"variant": "登录礼包", "stage_key": "activation_to_path_purchase"},
                    {"variant": "阶段任务", "stage_key": "activation_to_path_purchase"},
                ],
            },
        }
        base = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘路径"}},
            {"event_type": "SQL", "role": "assistant", "payload": payload},
        ]
        mistaken = base + [
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {"text": "两组的最低承接节点均是曝光到访问。q_12345678"},
            }
        ]
        careful = base + [
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {"text": "两组的最低承接节点均是关键行动到完整路径购买。q_12345678"},
            }
        ]

        self.assertFalse(score_trace(mistaken).checks["path_bottleneck_grounded"])
        self.assertTrue(score_trace(careful).checks["path_bottleneck_grounded"])

    def test_different_variant_bottlenecks_must_be_named_separately(self):
        payload = {
            "scope_confirmed": True,
            "scope_id": "scope-a",
            "metric_version": "v0.8",
            "evidence_id": "q_12345678",
            "path_diagnostics": {
                "status": "evaluated",
                "rows": [
                    {"variant": "A", "stage_key": "landing_to_claim"},
                    {"variant": "B", "stage_key": "activation_to_path_purchase"},
                ],
            },
        }
        base = [
            {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘路径"}},
            {"event_type": "SQL", "role": "assistant", "payload": payload},
        ]
        collapsed = base + [
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {"text": "最低承接节点是行动到完整购买。q_12345678"},
            }
        ]
        scoped = base + [
            {
                "event_type": "COMPLETE",
                "role": "assistant",
                "payload": {
                    "text": "A 的最低承接节点是访问到领取；B 的最低承接节点是关键行动到完整路径购买。q_12345678"
                },
            }
        ]

        self.assertFalse(score_trace(collapsed).checks["path_bottleneck_grounded"])
        self.assertTrue(score_trace(scoped).checks["path_bottleneck_grounded"])
