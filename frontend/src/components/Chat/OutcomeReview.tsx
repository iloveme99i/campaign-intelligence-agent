import { CheckCircle2, LoaderCircle, TriangleAlert } from "lucide-react";
import { useState } from "react";
import {
  recordDecisionOutcome,
  type DecisionCommit,
  type DecisionOutcomeRecord,
} from "@/api/merchant";

type ImplementationStatus = DecisionOutcomeRecord["implementation_status"];
type MeasurementMethod = DecisionOutcomeRecord["measurement_method"];
type GuardrailStatus = DecisionOutcomeRecord["guardrail_status"];
type NextDecision = DecisionOutcomeRecord["next_decision"];

const IMPLEMENTATION_NAMES: Record<ImplementationStatus, string> = {
  completed: "完整执行",
  partial: "部分执行",
  not_executed: "未执行",
};

const METHOD_NAMES: Record<MeasurementMethod, string> = {
  randomized_experiment: "预先规划的随机实验",
  holdout_comparison: "同期留出组对比",
  before_after: "执行前后对比",
};

const GUARDRAIL_NAMES: Record<GuardrailStatus, string> = {
  passed: "护栏通过",
  failed: "护栏触发",
  not_measured: "尚未测量",
};

const NEXT_DECISION_NAMES: Record<NextDecision, string> = {
  scale: "扩大执行",
  iterate: "调整后再验证",
  stop: "停止方案",
  collect_more_data: "继续收集数据",
};

const EVALUATION_NAMES: Record<string, string> = {
  decision_threshold_met: "达到决策门槛",
  statistical_but_below_mde: "未达到业务提升门槛",
  negative_effect: "出现显著负向影响",
  inconclusive: "结果暂无定论",
  guardrail_failed: "护栏未通过",
  observational_only: "仅形成方向性信号",
  randomization_unverified: "随机分流未核验",
  sample_ratio_mismatch: "样本比例异常",
  insufficient_expected_cells: "有效事件不足",
  unplanned_analysis: "未预先规划",
  planned_sample_not_reached: "未达到固定样本量",
};

const percent = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 2,
});
const decimal = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "always",
});

function toOptionalCount(value: string) {
  return value.trim() ? Number(value) : undefined;
}

export function OutcomeReview({
  conversationId,
  decision,
  recordedOutcome,
}: {
  conversationId: string;
  decision: DecisionCommit;
  recordedOutcome?: DecisionOutcomeRecord;
}) {
  const hasPlan = Boolean(decision.experiment_plan);
  const [outcome, setOutcome] = useState(recordedOutcome);
  const [implementationStatus, setImplementationStatus] =
    useState<ImplementationStatus>("completed");
  const [observedOn, setObservedOn] = useState("");
  const [measurementMethod, setMeasurementMethod] = useState<MeasurementMethod>(
    hasPlan ? "randomized_experiment" : "before_after",
  );
  const [randomizationVerified, setRandomizationVerified] = useState(false);
  const [guardrailStatus, setGuardrailStatus] =
    useState<GuardrailStatus>("not_measured");
  const [controlTotal, setControlTotal] = useState("");
  const [controlSuccesses, setControlSuccesses] = useState("");
  const [treatmentTotal, setTreatmentTotal] = useState("");
  const [treatmentSuccesses, setTreatmentSuccesses] = useState("");
  const [sourceReference, setSourceReference] = useState("");
  const [learning, setLearning] = useState("");
  const [nextDecision, setNextDecision] =
    useState<NextDecision>("collect_more_data");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  if (decision.decision_outcome === "rejected") return null;

  const executed = implementationStatus !== "not_executed";
  const metricName = {
    activation_per_claim: "完成激活",
    path_purchase_per_activation: "完成完整路径购买",
    purchase_per_exposure: "完成购买",
  }[decision.experiment_plan?.population.metric ?? "purchase_per_exposure"];

  return (
    <section className="border-t border-border py-5" aria-labelledby="outcome-review-title">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <span className="font-mono text-[11px] text-muted-foreground">07</span>
            <h3 id="outcome-review-title" className="text-sm font-semibold">
              执行结果回流
            </h3>
          </div>
          <p className="mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
            到复查节点后记录实际执行与测量结果。系统会检查样本、分流和护栏，避免把中途读数或前后对比写成确定效果。
          </p>
        </div>
        {outcome ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-700" />
        ) : (
          <span className="mt-0.5 whitespace-nowrap text-xs text-muted-foreground">
            复查日 {decision.review_date}
          </span>
        )}
      </div>

      {outcome ? (
        <div className="mt-4 border-y border-border py-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold">
              {outcome.evaluation
                ? EVALUATION_NAMES[outcome.evaluation.status] ?? "结果已记录"
                : "方案未执行"}
            </p>
            <span className="text-xs text-muted-foreground">
              {IMPLEMENTATION_NAMES[outcome.implementation_status]} · {outcome.observed_on}
            </span>
          </div>
          {outcome.evaluation && (
            <>
              <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3 text-sm sm:grid-cols-4">
                <div>
                  <dt className="text-xs text-muted-foreground">对照组</dt>
                  <dd className="mt-1 font-semibold tabular-nums">
                    {percent.format(outcome.evaluation.control_rate)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">处理组</dt>
                  <dd className="mt-1 font-semibold tabular-nums">
                    {percent.format(outcome.evaluation.treatment_rate)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">绝对差异</dt>
                  <dd className="mt-1 font-semibold tabular-nums">
                    {decimal.format(outcome.evaluation.difference_pp)} pp
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">因果判读</dt>
                  <dd className="mt-1 font-semibold">
                    {outcome.evaluation.causal_readout ? "满足条件" : "不满足"}
                  </dd>
                </div>
              </dl>
              <p className="mt-4 text-sm leading-6 text-foreground">
                {outcome.evaluation.conclusion}
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                95% 区间 {decimal.format(outcome.evaluation.ci95_difference_pp[0])} 至 {decimal.format(outcome.evaluation.ci95_difference_pp[1])} pp
                {outcome.evaluation.sample_coverage !== null
                  ? ` · 计划样本完成 ${percent.format(outcome.evaluation.sample_coverage)}`
                  : " · 未绑定预设样本规划"}
              </p>
            </>
          )}
          <dl className="mt-4 grid gap-3 border-t border-border pt-4 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">后续决定</dt>
              <dd className="mt-1 font-medium">{NEXT_DECISION_NAMES[outcome.next_decision]}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">数据依据</dt>
              <dd className="mt-1 break-words font-medium">{outcome.source_reference || "未执行，无结果数据"}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-xs text-muted-foreground">本轮学习</dt>
              <dd className="mt-1 leading-6">{outcome.learning}</dd>
            </div>
          </dl>
        </div>
      ) : (
        <form
          className="mt-4 space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            setSaving(true);
            setError("");
            void recordDecisionOutcome(conversationId, {
              observed_on: observedOn,
              implementation_status: implementationStatus,
              measurement_method: measurementMethod,
              randomization_verified:
                executed && measurementMethod === "randomized_experiment"
                  ? randomizationVerified
                  : false,
              ...(executed
                ? {
                    control_total: toOptionalCount(controlTotal),
                    control_successes: toOptionalCount(controlSuccesses),
                    treatment_total: toOptionalCount(treatmentTotal),
                    treatment_successes: toOptionalCount(treatmentSuccesses),
                  }
                : {}),
              guardrail_status: executed ? guardrailStatus : "not_measured",
              source_reference: executed ? sourceReference.trim() : "",
              learning: learning.trim(),
              next_decision: nextDecision,
              expected_decision_committed_at: decision.committed_at,
            })
              .then(setOutcome)
              .catch((cause) =>
                setError(
                  cause instanceof Error
                    ? cause.message
                    : "执行结果保存失败，请重试。",
                ),
              )
              .finally(() => setSaving(false));
          }}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-medium text-muted-foreground">
              实际执行状态
              <select
                value={implementationStatus}
                onChange={(event) =>
                  setImplementationStatus(event.target.value as ImplementationStatus)
                }
                className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
              >
                {Object.entries(IMPLEMENTATION_NAMES).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <label className="text-xs font-medium text-muted-foreground">
              结果观察日期
              <input
                required
                type="date"
                max={new Date().toISOString().slice(0, 10)}
                value={observedOn}
                onChange={(event) => setObservedOn(event.target.value)}
                className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
              />
            </label>
          </div>

          {executed && (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs font-medium text-muted-foreground">
                  测量设计
                  <select
                    value={measurementMethod}
                    onChange={(event) => {
                      setMeasurementMethod(event.target.value as MeasurementMethod);
                      setRandomizationVerified(false);
                    }}
                    className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  >
                    <option value="randomized_experiment" disabled={!hasPlan}>
                      {METHOD_NAMES.randomized_experiment}{hasPlan ? "" : "（未预先规划）"}
                    </option>
                    <option value="holdout_comparison">{METHOD_NAMES.holdout_comparison}</option>
                    <option value="before_after">{METHOD_NAMES.before_after}</option>
                  </select>
                </label>
                <label className="text-xs font-medium text-muted-foreground">
                  业务护栏
                  <select
                    value={guardrailStatus}
                    onChange={(event) => setGuardrailStatus(event.target.value as GuardrailStatus)}
                    className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  >
                    {Object.entries(GUARDRAIL_NAMES).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </label>
              </div>

              {measurementMethod === "randomized_experiment" && (
                <label className="flex items-start gap-2.5 text-sm leading-6 text-foreground">
                  <input
                    type="checkbox"
                    checked={randomizationVerified}
                    onChange={(event) => setRandomizationVerified(event.target.checked)}
                    className="mt-1 h-4 w-4 rounded border-border accent-[var(--primary)]"
                  />
                  已核验随机化单元、分流时点与串组情况；勾选不是形式确认，异常样本比例仍会被系统拦截。
                </label>
              )}

              <fieldset>
                <legend className="text-xs font-medium text-muted-foreground">
                  主指标样本 · {metricName}
                </legend>
                <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {[
                    ["对照组合格", controlTotal, setControlTotal],
                    [`对照组${metricName}`, controlSuccesses, setControlSuccesses],
                    ["处理组合格", treatmentTotal, setTreatmentTotal],
                    [`处理组${metricName}`, treatmentSuccesses, setTreatmentSuccesses],
                  ].map(([label, value, setter]) => (
                    <label key={String(label)} className="text-xs text-muted-foreground">
                      {String(label)}
                      <input
                        required
                        type="number"
                        min="0"
                        step="1"
                        inputMode="numeric"
                        value={value as string}
                        onChange={(event) =>
                          (setter as React.Dispatch<React.SetStateAction<string>>)(event.target.value)
                        }
                        className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm tabular-nums text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      />
                    </label>
                  ))}
                </div>
              </fieldset>

              <label className="block text-xs font-medium text-muted-foreground">
                数据依据
                <input
                  required
                  maxLength={500}
                  value={sourceReference}
                  onChange={(event) => setSourceReference(event.target.value)}
                  placeholder="实验编号、查询链接或分析报告版本"
                  className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                />
              </label>
            </>
          )}

          <label className="block text-xs font-medium text-muted-foreground">
            本轮业务复盘
            <textarea
              required
              rows={3}
              maxLength={2000}
              value={learning}
              onChange={(event) => setLearning(event.target.value)}
              placeholder={executed ? "记录实际结果、异常与原判断之间的差异" : "说明未执行的具体原因和失效假设"}
              className="mt-1.5 block w-full resize-y rounded-md border border-border bg-background p-3 text-sm leading-6 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
            />
          </label>

          <label className="block text-xs font-medium text-muted-foreground sm:max-w-[calc(50%-0.375rem)]">
            基于结果的下一步
            <select
              value={nextDecision}
              onChange={(event) => setNextDecision(event.target.value as NextDecision)}
              className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              {Object.entries(NEXT_DECISION_NAMES).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>

          {error && (
            <div className="flex gap-2 text-sm leading-6 text-red-700" role="alert">
              <TriangleAlert className="mt-1 h-4 w-4 shrink-0" />
              <p>{error}</p>
            </div>
          )}
          <button
            type="submit"
            disabled={
              saving ||
              !observedOn ||
              !learning.trim() ||
              (executed &&
                (!sourceReference.trim() ||
                  !controlTotal.trim() ||
                  !controlSuccesses.trim() ||
                  !treatmentTotal.trim() ||
                  !treatmentSuccesses.trim()))
            }
            className="inline-flex h-10 items-center rounded-md bg-foreground px-4 text-sm font-semibold text-background outline-none hover:opacity-90 focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-45"
          >
            {saving && <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {saving ? "正在核验并保存…" : "核验结果并回流"}
          </button>
        </form>
      )}
    </section>
  );
}
