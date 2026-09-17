import { Check, ChevronDown, ShieldAlert, ShieldCheck } from "lucide-react";
import type { TraceQuality } from "@/api/merchant";

const integer = new Intl.NumberFormat("zh-CN");

const FAILURE_GUIDANCE: Record<string, string> = {
  confirmed_scope: "复盘口径尚未确认",
  deterministic_before_answer: "结论早于确定性核算",
  complete_query_results: "查询结果被截断",
  single_scope: "混用了不同复盘口径",
  diagnostic_scope_consistent: "定向诊断与总盘口径不一致",
  evidence_cited: "引用了不存在的证据",
  core_evidence_covered: "目标、经营结果、路径或成本证据不完整",
  numeric_facts_cited: "事实数字缺少同行证据",
  numeric_conclusion_cited: "结论数字缺少同行证据",
  diagnosis_evidence_cited: "定向诊断未引用本次诊断证据",
  diagnosis_supports_conclusion: "行动判断缺少诊断证据支持",
  discount_amount_sanity: "优惠金额超出活动期总额",
  money_unit_scale_sanity: "金额单位或数量级异常",
  money_values_grounded: "金额与确定性证据不一致",
  task_driven_diagnosis: "尚未完成任务所需的定向诊断",
  diagnostic_reason_recorded: "定向诊断缺少业务理由",
  diagnostic_selective: "诊断范围过宽或存在重复下钻",
  diagnosis_before_answer: "诊断发生在最终结论之后",
  diagnosis_matches_task: "诊断方向与本轮决策问题不一致",
  answer_present: "尚未生成完整结论",
  decision_coverage: "结论未覆盖目标、路径、成本与下一步",
  fact_hypothesis_separation: "事实、假设与待验证项未分开",
  action_contract: "下一轮行动缺少对象、指标、护栏或停止条件",
  causal_boundary: "未说明前后变化不能证明因果",
  incrementality_estimand_clarity: "ATT 未说清经营单元与观测周期口径",
  incrementality_pretrend_power_clarity: "前趋势检验的证据强度被高估",
  target_cost_tension: "目标达成与贡献下降的矛盾未被说明",
  cost_attribution_boundary: "共享成本归属边界不清",
  no_empty_advice: "行动建议过于空泛",
  no_internal_field_leakage: "结论包含用户不需要的内部字段",
  no_raw_money_units: "金额未统一换算为元",
  no_unsupported_causal_claim: "存在未经验证的因果表述",
  variant_allocation_boundary: "观察性差异被直接用于资源分配",
  no_unqualified_forecast: "存在无预测依据的确定性承诺",
  decision_intent_contract: "未回答本轮决策问题或说明成立条件",
  single_executable_primary_metric: "实验主指标不唯一或不可计算",
  path_stage_consistency: "路径阶段解释与人数关系矛盾",
  path_bottleneck_grounded: "最低承接节点与确定性诊断不一致",
  experiment_control_cohort: "实验对照不在同一合格人群内",
  rate_difference_arithmetic: "百分点差与展示比率不一致",
  statistical_metric_lineage: "统计结论挂到了错误指标上",
};

export function TraceAudit({ audit }: { audit?: TraceQuality }) {
  if (!audit) return null;
  const passed = audit.status === "passed";

  return (
    <details className="group mb-4 border-y border-border py-3">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-primary">
        <span className="inline-flex min-w-0 items-center gap-2 text-xs font-semibold">
          {passed ? (
            <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-700" />
          ) : (
            <ShieldAlert className="h-4 w-4 shrink-0 text-amber-700" />
          )}
          运行审计
          <span
            className={passed ? "text-emerald-700" : "text-amber-800"}
          >
            {audit.passed}/{audit.total} {passed ? "通过" : "需复核"}
          </span>
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-180" />
      </summary>

      <div className="mt-4 border-t border-border pt-4">
        <div className="grid grid-cols-2 gap-x-5 gap-y-3">
          {audit.groups.map((item) => (
            <div
              key={item.key}
              className="flex items-center justify-between gap-3 text-xs"
            >
              <span className="text-muted-foreground">{item.label}</span>
              <span className="inline-flex items-center gap-1 font-semibold tabular-nums">
                {item.passed === item.total && (
                  <Check className="h-3.5 w-3.5 text-emerald-700" />
                )}
                {item.passed}/{item.total}
              </span>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[11px] leading-5 text-muted-foreground">
          {audit.evidence_count} 项证据 · {audit.model_calls} 次模型调用
          {audit.elapsed_seconds !== null &&
            ` · ${audit.elapsed_seconds.toFixed(1)} 秒`}
          {` · ${integer.format(audit.total_tokens)} tokens`}
        </p>
        {audit.quality_retry && (
          <p className="mt-2 text-[11px] leading-5 text-muted-foreground">
            质量修复 1 次 · 初稿 {audit.quality_retry.original_passed}/{audit.quality_retry.total}
            {` → `}
            修正版 {audit.quality_retry.repaired_passed}/{audit.quality_retry.total}
            {audit.quality_retry.status === "accepted"
              ? "，全部门禁通过，已采用修正版"
              : "，仍有门禁未通过，未替换"}
          </p>
        )}
        {!passed && audit.failed_checks.length > 0 && (
          <div
            className="mt-4 border-y border-amber-300 bg-amber-50/70 py-3 text-amber-950"
            role="status"
            aria-label="本轮未通过的审计项"
          >
            <p className="text-xs font-semibold">采用前需要修正</p>
            <ul className="mt-2 space-y-1 text-xs leading-5">
              {audit.failed_checks.map((check) => (
                <li key={check} className="flex gap-2">
                  <span aria-hidden="true" className="mt-[0.55rem] h-1 w-1 shrink-0 rounded-full bg-amber-700" />
                  <span>{FAILURE_GUIDANCE[check] ?? "存在未通过的质量检查"}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
          {passed
            ? "审计已核对口径、证据、诊断与表述边界；仍需业务负责人确认。"
            : "本轮已被拦截，修正后重新运行审计；不要直接采用或落档。"}
        </p>
      </div>
    </details>
  );
}
