import { useRef, useState } from "react";
import { Check, FlaskConical, LoaderCircle, TriangleAlert } from "lucide-react";
import { planExperiment, type ExperimentPlan as Plan } from "@/api/merchant";
import { merchantName } from "@/lib/merchantNames";

const integer = new Intl.NumberFormat("zh-CN");
const percent = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 2,
});
type Dimension = Plan["population"]["dimension"];
type Metric = Plan["population"]["metric"];
const dimensions: Record<Dimension, string> = {
  all: "整个活动范围", variant: "方案组", channel: "渠道", location_id: "经营点", audience: "人群",
};
const metrics: Record<Metric, string> = {
  purchase_per_exposure: "购买用户 / 曝光用户",
  activation_per_claim: "激活用户 / 权益领取用户",
  path_purchase_per_activation: "完整路径购买 / 已完成关键行动",
};

export function ExperimentPlan({
  conversationId,
  expectedScopeId,
  expectedAnswer,
  committedPlan,
  decisionLocked,
  onPlanConfirmed,
  segments,
}: {
  conversationId: string;
  expectedScopeId: string;
  expectedAnswer: string;
  committedPlan?: Plan;
  decisionLocked: boolean;
  onPlanConfirmed: (plan?: Plan) => void;
  segments?: Record<string, Array<{ dimension_value: string; total_groups?: number }>>;
}) {
  const [mde, setMde] = useState(String(committedPlan?.mde_pp ?? 1));
  const [traffic, setTraffic] = useState(
    String((committedPlan?.traffic_share ?? 1) * 100),
  );
  const [plan, setPlan] = useState<Plan | undefined>(committedPlan);
  const [confirmed, setConfirmed] = useState(Boolean(committedPlan));
  const [loading, setLoading] = useState(false);
  const requestVersion = useRef(0);
  const [error, setError] = useState("");
  const [dimension, setDimension] = useState<Dimension | "">(committedPlan?.population.dimension ?? "");
  const [segment, setSegment] = useState(committedPlan?.population.value ?? "");
  const [secondaryDimension, setSecondaryDimension] = useState<Exclude<Dimension, "all"> | "">(committedPlan?.population.secondary_dimension ?? "");
  const [secondarySegment, setSecondarySegment] = useState(committedPlan?.population.secondary_value ?? "");
  const [metric, setMetric] = useState<Metric>(committedPlan?.population.metric ?? "purchase_per_exposure");
  const available = dimension && dimension !== "all" ? segments?.[dimension] ?? [] : [];
  const secondaryAvailable = secondaryDimension ? segments?.[secondaryDimension] ?? [] : [];
  const invalidate = () => {
    requestVersion.current += 1;
    setLoading(false);
    setPlan(undefined);
    setConfirmed(false);
    onPlanConfirmed(undefined);
    setError("");
  };

  const calculate = (mdePp: number, trafficPercent: number) => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError("");
    return planExperiment(conversationId, {
      mde_pp: mdePp,
      traffic_share: trafficPercent / 100,
      expected_scope_id: expectedScopeId,
      expected_answer: expectedAnswer,
      planning_dimension: dimension as Dimension,
      planning_value: dimension === "all" ? null : segment,
      planning_secondary_dimension: secondaryDimension || null,
      planning_secondary_value: secondaryDimension ? secondarySegment : null,
      planning_metric: metric,
    })
      .then((result) => {
        if (version !== requestVersion.current) return;
        setPlan(result);
        setConfirmed(true);
        onPlanConfirmed(result);
      })
      .catch((cause) => {
        if (version !== requestVersion.current) return;
        setError(
          cause instanceof Error ? cause.message : "实验样本测算失败，请重试。",
        );
      })
      .finally(() => {
        if (version === requestVersion.current) setLoading(false);
      });
  };

  return (
    <section
      className="border-t border-border py-5"
      aria-labelledby="experiment-plan-title"
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] text-muted-foreground">
              05
            </span>
            <h3 id="experiment-plan-title" className="text-sm font-semibold">
              下一轮实验设计
            </h3>
          </div>
          <p className="mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
            先确认下一轮实际干预的人群和主指标，再按该群体的合格流量测算。全活动数据不能代替定向试点。
          </p>
        </div>
        <FlaskConical
          className="mt-0.5 h-4 w-4 shrink-0 text-primary"
          aria-hidden="true"
        />
      </div>

      {!decisionLocked && (
        <div className="mt-4 grid grid-cols-2 gap-3">
          <label className="text-xs font-medium text-muted-foreground">实验对象
            <select value={dimension} onChange={(event) => { setDimension(event.target.value as Dimension); setSegment(""); setSecondaryDimension(""); setSecondarySegment(""); invalidate(); }} className="mt-1.5 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground">
              <option value="">选择对象</option>
              {Object.entries(dimensions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="text-xs font-medium text-muted-foreground">具体分组
            <select value={segment} disabled={!dimension || dimension === "all"} onChange={(event) => { setSegment(event.target.value); invalidate(); }} className="mt-1.5 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground disabled:opacity-60">
              <option value="">{dimension === "all" ? "全部合格用户" : "选择分组"}</option>
              {available.map((row) => <option key={row.dimension_value} value={row.dimension_value}>{merchantName(row.dimension_value)}</option>)}
            </select>
          </label>
          {dimension && dimension !== "all" && <>
            <label className="text-xs font-medium text-muted-foreground">叠加筛选 · 可选
              <select value={secondaryDimension} onChange={(event) => { setSecondaryDimension(event.target.value as Exclude<Dimension, "all"> | ""); setSecondarySegment(""); invalidate(); }} className="mt-1.5 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground">
                <option value="">不叠加</option>
                {Object.entries(dimensions).filter(([value]) => value !== "all" && value !== dimension).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="text-xs font-medium text-muted-foreground">交集分组
              <select value={secondarySegment} disabled={!secondaryDimension} onChange={(event) => { setSecondarySegment(event.target.value); invalidate(); }} className="mt-1.5 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground disabled:opacity-60">
                <option value="">{secondaryDimension ? "选择交集分组" : "无需选择"}</option>
                {secondaryAvailable.map((row) => <option key={row.dimension_value} value={row.dimension_value}>{merchantName(row.dimension_value)}</option>)}
              </select>
            </label>
          </>}
          <label className="col-span-2 text-xs font-medium text-muted-foreground">实验主指标
            <select value={metric} onChange={(event) => { setMetric(event.target.value as Metric); invalidate(); }} className="mt-1.5 h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground">
              {Object.entries(metrics).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
        </div>
      )}

      {!decisionLocked && (
        <form
          className="mt-4 grid grid-cols-2 items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            const mdeValue = Number(mde);
            const trafficValue = Number(traffic);
            if (
              !(
                dimension && (dimension === "all" || segment) &&
                (!secondaryDimension || secondarySegment) &&
                mdeValue > 0 &&
                mdeValue < 100 &&
                trafficValue > 0 &&
                trafficValue <= 100
              )
            ) {
              setError("先确定实验对象与分组；MDE 需大于 0 且小于 100，流量需在 1%–100% 之间。");
              return;
            }
            void calculate(mdeValue, trafficValue);
          }}
        >
          <label className="text-xs font-medium text-muted-foreground">
            最小可检测提升
            <span className="relative mt-1.5 block">
              <input
                type="number"
                min="0.1"
                max="99.9"
                step="0.1"
                value={mde}
                onChange={(event) => { setMde(event.target.value); invalidate(); }}
                className="h-10 w-full rounded-md border border-border bg-background px-3 pr-9 text-sm tabular-nums text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                aria-describedby="mde-unit"
              />
              <span
                id="mde-unit"
                className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground"
              >
                pp
              </span>
            </span>
          </label>
          <label className="text-xs font-medium text-muted-foreground">
            用于实验的流量
            <span className="relative mt-1.5 block">
              <input
                type="number"
                min="1"
                max="100"
                step="1"
                value={traffic}
                onChange={(event) => { setTraffic(event.target.value); invalidate(); }}
                className="h-10 w-full rounded-md border border-border bg-background px-3 pr-9 text-sm tabular-nums text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                aria-describedby="traffic-unit"
              />
              <span
                id="traffic-unit"
                className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground"
              >
                %
              </span>
            </span>
          </label>
          <button
            type="submit"
            disabled={loading || !dimension || (dimension !== "all" && !segment) || (secondaryDimension !== "" && !secondarySegment)}
            className="col-span-2 inline-flex h-10 items-center justify-center rounded-md border border-border bg-background px-3 text-sm font-semibold outline-none hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-wait disabled:opacity-50"
          >
            {loading && (
              <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            )}
            {loading ? "测算中" : confirmed ? "重新测算" : "确认并测算"}
          </button>
        </form>
      )}

      {error && (
        <p className="mt-3 text-sm leading-6 text-red-700" role="alert">
          {error}
        </p>
      )}

      {!decisionLocked && <p className="mt-3 text-xs leading-5 text-muted-foreground">交集人数会从同一快照重新核算，不相乘单维度人数。随机分流须发生在主指标分母计入前，干预不能改变谁有资格进入分母；否则改用更早的分流人群与指标。若整店统一调整，不能凭用户级测算声称 A/B 效果。</p>}

      {plan && (
        <div className="mt-5">
          <p className="mb-2 text-sm font-semibold text-foreground">{dimensions[plan.population.dimension]}{plan.population.value ? ` · ${merchantName(plan.population.value)}` : ""}{plan.population.secondary_dimension ? ` × ${dimensions[plan.population.secondary_dimension]} · ${merchantName(plan.population.secondary_value ?? "")}` : ""} · {metrics[plan.population.metric]}</p>
          <p className="mb-3 text-xs text-muted-foreground">当前观察样本：{integer.format(plan.population.eligible_users)} 名合格用户，其中 {integer.format(plan.population.successful_users)} 名完成主指标。实验期只能在相同目标对象与同一指标下使用此测算。</p>
          <p
            className={`mb-3 text-xs font-medium ${committedPlan || confirmed ? "text-emerald-700" : "text-amber-800"}`}
          >
            {committedPlan
              ? "实验规划已随本轮决策落档"
              : decisionLocked
                ? "该历史决策未绑定实验规划"
                : confirmed
                  ? "规划参数已确认，将随本轮决策落档"
                  : "默认测算仅供预览，确认参数后才会随决策落档"}
          </p>
          <dl className="grid grid-cols-2 border-y border-border sm:grid-cols-4 sm:divide-x sm:divide-border">
            <div className="py-3 pr-3 sm:pr-4">
              <dt className="text-xs text-muted-foreground">当前基线</dt>
              <dd className="mt-1 text-sm font-semibold tabular-nums">
                {percent.format(plan.baseline_rate)}
              </dd>
            </div>
            <div className="py-3 pl-3 sm:px-4">
              <dt className="text-xs text-muted-foreground">目标可检测至</dt>
              <dd className="mt-1 text-sm font-semibold tabular-nums">
                {percent.format(plan.target_rate)}
              </dd>
            </div>
            <div className="border-t border-border py-3 pr-3 sm:border-t-0 sm:px-4">
              <dt className="text-xs text-muted-foreground">每组样本</dt>
              <dd className="mt-1 text-sm font-semibold tabular-nums">
                {integer.format(plan.required_per_group)} 人
              </dd>
            </div>
            <div className="border-t border-border py-3 pl-3 sm:border-t-0 sm:pl-4">
              <dt className="text-xs text-muted-foreground">预计周期</dt>
              <dd className="mt-1 text-sm font-semibold tabular-nums">
                {integer.format(plan.estimated_days)} 天
              </dd>
            </div>
          </dl>

          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            50/50 分组 · 双侧 α={plan.alpha} · 检验效能{" "}
            {percent.format(plan.power)}。{plan.planning_basis}
          </p>

          {plan.feasibility.status === "exceeds_observed_window" && (
            <div className="mt-4 flex gap-2.5 rounded-lg border border-amber-300/70 bg-amber-50 px-3 py-3 text-sm text-amber-950">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <div>
                <p className="font-semibold">当前流量下，无法在同等活动周期完成实验</p>
                <p className="mt-1 text-xs leading-5">{plan.feasibility.detail}</p>
              </div>
            </div>
          )}

          <ul className="mt-4 divide-y divide-border border-y border-border">
            {plan.readiness.map((item) => {
              const confirmed = item.status === "user_confirmed_input";
              return (
                <li key={item.key} className="flex gap-3 py-3 text-xs">
                  {confirmed ? (
                    <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-700" />
                  ) : (
                    <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700" />
                  )}
                  <div className="min-w-0">
                    <p className="font-semibold">{item.label}</p>
                    <p className="mt-1 leading-5 text-muted-foreground">
                      {item.detail}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>

          <p className="mt-3 text-xs leading-5 text-foreground">
            {plan.decision_rule}
          </p>
        </div>
      )}
    </section>
  );
}
