/* Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V4 */
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Check,
  Minus,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import type { UIMessage } from "@/types";
import type { ReviewScope } from "@/types/merchant";
import { variantName } from "@/lib/merchantNames";
import { DiagnosticReadout } from "./DiagnosticReadout";
import { TrendAnalysis, type TrendRow } from "./TrendAnalysis";
import { SegmentMatrix, type SegmentResults } from "./SegmentMatrix";
import { DataReliability, type CoverageReadout, type QualityReadout } from "./DataReliability";

type ComparisonRow = {
  period: "baseline" | "activity";
  days: number;
  completed_orders: number;
  buyer_users: number;
  net_revenue_cents: number;
  merchant_discount_cents: number;
  refund_cents: number;
  contribution_cents: number;
};

/** Symmetric accounting identity on daily contribution, not causal attribution. */
export function decomposeDailyContribution(before: ComparisonRow, after: ComparisonRow) {
  if (before.days <= 0 || after.days <= 0 || before.completed_orders <= 0 || after.completed_orders <= 0) return null;
  const beforeOrdersPerDay = before.completed_orders / before.days;
  const afterOrdersPerDay = after.completed_orders / after.days;
  const beforePerOrder = before.contribution_cents / before.completed_orders;
  const afterPerOrder = after.contribution_cents / after.completed_orders;
  const volumeCentsPerDay = (afterOrdersPerDay - beforeOrdersPerDay) * (beforePerOrder + afterPerOrder) / 2;
  const unitCentsPerDay = (afterPerOrder - beforePerOrder) * (beforeOrdersPerDay + afterOrdersPerDay) / 2;
  return { volumeCentsPerDay, unitCentsPerDay, deltaCentsPerDay: after.contribution_cents / after.days - before.contribution_cents / before.days };
}
type FunnelRow = {
  variant: string;
  exposed_users: number;
  landing_users: number;
  claim_users: number;
  activated_users: number;
  buyer_users: number;
  path_buyer_users?: number;
  activated_without_path_purchase_users?: number;
  activation_to_path_purchase_rate?: number | null;
  completed_orders: number;
  net_revenue_cents: number;
  contribution_cents: number;
};
type PathDiagnosticRow = {
  variant: string;
  stage_key: string;
  stage_label: string;
  from_users: number;
  to_users: number;
  continuation_rate: number;
  not_continued_users: number;
};
type PathDiagnostics = {
  status: "evaluated" | "invalid_sequence";
  basis: "lowest_adjacent_stage_continuation";
  rows: PathDiagnosticRow[];
  invalid_variants?: string[];
  interpretation?: string;
  evidence_id?: string;
};
type CostRow = { variant: string; channel?: string; activity_cost_cents: number };
type CostBoundary = {
  status: "fully_scoped" | "shared_costs_unallocated";
  attributable_cost_cents: number;
  shared_cost_cents: number;
  detail: string;
};
type ExperimentComparison = {
  control: string;
  treatment: string;
  control_rate: number;
  treatment_rate: number;
  difference_pp: number;
  ci95_difference_pp: [number, number];
  p_value: number;
  descriptive_signal: boolean;
  sample_check: "ok" | "small_expected_cell";
};
type ExperimentReadout = {
  status: "observational_readout" | "insufficient_groups" | "contaminated_groups" | "unlinked_buyers" | "dimension_mismatch" | "invalid_group_population";
  assumption: string;
  method?: string;
  comparisons: ExperimentComparison[];
};
type IncrementalityReadiness = {
  status: "not_identified" | "randomized_ready" | "did_ready";
  current_design: string;
  recommended_method: string | null;
  detail: string;
  randomized_experiment: { ready: boolean; missing: string[] };
  difference_in_differences: { ready: boolean; missing: string[]; required_validation: string[] };
  prohibited_shortcuts: string[];
};
type IncrementalityEstimate = {
  status: "identified" | "validation_failed" | "invalid_panel";
  metric: string;
  estimate?: number;
  effect_interpretation?: string;
  ci95?: [number, number];
  p_value?: number;
  degrees_of_freedom?: number | null;
  inference?: "welch_t_small_sample" | "degenerate_no_variance";
  relative_to_control_pre?: number | null;
  treated_units?: number;
  control_units?: number;
  pre_periods?: number;
  post_periods?: number;
  pretrend_power?: "low" | "standard";
  pretrend_warning?: string | null;
  decision_ready: boolean;
  parallel_trends?: { status: "passed" | "failed"; p_value: number };
  placebo_test?: { status: "passed" | "failed"; p_value: number };
  causal_boundary?: string;
  detail?: string;
};
type IncrementalityResult = {
  status: "not_supplied" | "not_estimated" | "identified" | "validation_failed";
  detail: string;
  evidence_id?: string;
  estimates: IncrementalityEstimate[];
};
export function incrementalityBoundary(result?: IncrementalityResult) {
  const identified =
    result?.status === "identified" &&
    result.estimates.length > 0 &&
    result.estimates.every((estimate) => estimate.decision_ready);
  return {
    identified,
    label: identified ? "条件性增量已识别" : "描述性复盘",
    detail: identified
      ? "DiD 假设与适用范围须持续核对"
      : "实验随机性仍需业务侧验证",
  };
}
type TargetReadout = {
  status:
    | "evaluated"
    | "missing_denominator"
    | "missing_cost_allocation"
    | "invalid_path_population"
    | "invalid_target"
    | "unsupported_metric";
  metric: "conversion_rate" | "completed_orders" | "net_revenue" | "roi";
  actual_value?: number | null;
  target_value?: number;
  unit?: "rate" | "count" | "cents" | "ratio";
  achieved?: boolean | null;
  gap?: number | null;
};
type EvidenceManifestEntry = {
  kind: "outcome" | "funnel" | "cost" | "target" | "incrementality";
  evidence_id: string;
  row_count?: number;
  elapsed_ms?: number;
  truncated?: boolean;
};
type AnalysisManifestEntry = { kind: string; evidence_id: string };
type AttributionTail = {
  completed_orders: number;
  buyer_users: number;
  net_revenue_cents: number;
  contribution_cents: number;
  candidate_orders?: number;
  orders_without_matching_exposure?: number;
  orders_outside_exposure_window?: number;
};


function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
function parseRows(message?: UIMessage) {
  const payload = message?.payload;
  if (!payload || !Array.isArray(payload.rows)) return null;
  const rows = payload.rows as unknown as ComparisonRow[];
  if (
    rows.length !== 2 ||
    rows.some(
      (row) =>
        !row ||
        !["baseline", "activity"].includes(row.period) ||
        !finite(row.completed_orders) ||
        !finite(row.net_revenue_cents) ||
        !finite(row.contribution_cents),
    )
  )
    return null;
  return {
    rows,
    funnel: (Array.isArray(payload.funnel)
      ? payload.funnel
      : []) as FunnelRow[],
    pathDiagnostics: payload.path_diagnostics && typeof payload.path_diagnostics === "object"
      ? payload.path_diagnostics as PathDiagnostics : undefined,
    timeline: (Array.isArray(payload.timeline) ? payload.timeline : []) as TrendRow[],
    timelineBinDays: payload.timeline_bin_days === 7 ? 7 : 1,
    segments: (payload.segments && typeof payload.segments === "object"
      ? payload.segments : {}) as SegmentResults,
    dataQuality: payload.data_quality && typeof payload.data_quality === "object"
      ? payload.data_quality as QualityReadout : undefined,
    dataCoverage: payload.data_coverage && typeof payload.data_coverage === "object"
      ? payload.data_coverage as CoverageReadout : undefined,
    attributionTail: payload.attribution_tail && typeof payload.attribution_tail === "object"
      ? payload.attribution_tail as AttributionTail : undefined,
    attributionDays: typeof payload.attribution_days_configured === "number"
      ? payload.attribution_days_configured : undefined,
    attributionTailStatus: payload.attribution_tail_status,
    analysisManifest: Array.isArray(payload.analysis_manifest)
      ? payload.analysis_manifest.filter((entry): entry is AnalysisManifestEntry =>
          Boolean(entry && typeof entry === "object" &&
            typeof (entry as AnalysisManifestEntry).kind === "string" &&
            typeof (entry as AnalysisManifestEntry).evidence_id === "string"))
      : [],
    costs: (Array.isArray(payload.costs) ? payload.costs : []) as CostRow[],
    costBoundary: payload.cost_boundary as CostBoundary | undefined,
    experiment: payload.experiment as ExperimentReadout | undefined,
    incrementalityReadiness: payload.incrementality_readiness as IncrementalityReadiness | undefined,
    incrementality: payload.incrementality as IncrementalityResult | undefined,
    target: payload.target as TargetReadout | undefined,
    provenance:
      payload.provenance === "synthetic" || payload.provenance === "imported"
        ? payload.provenance
        : undefined,
    metricVersion:
      typeof payload.metric_version === "string" ? payload.metric_version : undefined,
    scope: payload.scope as ReviewScope | undefined,
    scopeId:
      typeof payload.scope_id === "string" ? payload.scope_id : undefined,
    snapshotId:
      typeof payload.snapshot_id === "string" ? payload.snapshot_id : undefined,
    relatedEvidenceIds: Array.isArray(payload.related_evidence_ids)
      ? payload.related_evidence_ids.filter(
          (value): value is string => typeof value === "string",
        )
      : [],
    evidenceManifest: Array.isArray(payload.evidence_manifest)
      ? (payload.evidence_manifest.filter(
          (entry) =>
            entry &&
            typeof entry === "object" &&
            typeof (entry as EvidenceManifestEntry).evidence_id === "string",
        ) as EvidenceManifestEntry[])
      : [],
    warnings: Array.isArray(payload.warnings)
      ? payload.warnings.filter(
          (value): value is string => typeof value === "string",
        )
      : [],
    sql: typeof payload.sql === "string" ? payload.sql : undefined,
    parameters:
      payload.parameters && typeof payload.parameters === "object"
        ? (payload.parameters as Record<string, unknown>)
        : undefined,
    elapsedMs:
      typeof payload.elapsed_ms === "number" ? payload.elapsed_ms : undefined,
    truncated:
      typeof payload.truncated === "boolean" ? payload.truncated : undefined,
  };
}

const currency = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  maximumFractionDigits: 0,
});
const integer = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  maximumFractionDigits: 1,
});
const money = (value: number) => currency.format(value / 100);
const ratio = (part: number, total: number) =>
  total > 0 ? part / total : null;
const percentOrDash = (value: number | null) =>
  value == null ? "—" : percent.format(value);
const signed = (value: number, render: (value: number) => string) =>
  value === 0 ? "持平" : `${value > 0 ? "+" : "−"}${render(Math.abs(value))}`;
const percentagePoints = (value: number) =>
  Math.abs(value) < 0.1 ? value.toFixed(2) : value.toFixed(1);
const targetNames: Record<TargetReadout["metric"], string> = {
  conversion_rate: "购买转化率",
  completed_orders: "完成订单",
  net_revenue: "净收入",
  roi: "贡献/活动成本",
};
const evidenceKindNames: Record<EvidenceManifestEntry["kind"], string> = {
  outcome: "经营结果",
  funnel: "用户路径",
  cost: "活动成本",
  target: "目标配置",
  incrementality: "增量识别",
};
const incrementalityMetricNames: Record<string, string> = {
  completed_orders: "完成订单",
  buyer_users: "购买用户",
  net_revenue_cents: "净收入",
  contribution_cents: "贡献额",
};
const incrementalityMetricOrder = [
  "completed_orders",
  "buyer_users",
  "net_revenue_cents",
  "contribution_cents",
];
function incrementalityValue(metric: string, value?: number) {
  if (value == null) return "—";
  const render = metric.endsWith("_cents")
    ? (number: number) => currency.format(number / 100)
    : (number: number) =>
        new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(number);
  return signed(value, render);
}
function probability(value?: number) {
  if (value == null) return "—";
  return value < 0.001 ? "<0.001" : value.toFixed(3);
}
function targetNumber(value: number, unit?: TargetReadout["unit"]) {
  if (unit === "rate") return percent.format(value);
  if (unit === "cents") return money(value);
  if (unit === "ratio") return `${value.toFixed(2)}×`;
  return integer.format(value);
}

function DeltaIcon({ value }: { value: number }) {
  if (value > 0)
    return <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />;
  if (value < 0)
    return <ArrowDownRight className="h-3.5 w-3.5" aria-hidden="true" />;
  return <Minus className="h-3.5 w-3.5" aria-hidden="true" />;
}

function filterSummary(scope?: ReviewScope) {
  if (!scope) return "—";
  const filters = [
    ["实验组", scope.variant],
    ["渠道", scope.channel],
    ["经营点", scope.location_id],
    ["人群", scope.audience],
  ]
    .filter(([, value]) => value)
    .map(([label, value]) => `${label}：${value}`);
  return filters.length > 0
    ? filters.join(" · ")
    : "全部实验组、渠道、经营点与人群";
}

function Metric({
  label,
  before,
  after,
  format,
  comparable = true,
}: {
  label: string;
  before: number;
  after: number;
  format: (value: number) => string;
  comparable?: boolean;
}) {
  const delta = after - before;
  return (
    <div className="metric-cell min-w-0 py-4 pr-4 sm:py-5">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-2 truncate text-[1.35rem] font-semibold tabular-nums tracking-[-0.025em]">
        {format(after)}
      </dd>
      <p
        className={`mt-1.5 flex items-center gap-1 text-xs tabular-nums ${delta < 0 ? "text-red-700" : delta > 0 ? "text-emerald-700" : "text-muted-foreground"}`}
      >
        {comparable ? <><DeltaIcon value={delta} /><span>较对比期 {signed(delta, format)}</span></> : <span>对比期 {format(before)} · 周期不同</span>}
      </p>
    </div>
  );
}

const funnelSteps: Array<{ key: keyof FunnelRow; label: string }> = [
  { key: "exposed_users", label: "曝光" },
  { key: "landing_users", label: "访问" },
  { key: "claim_users", label: "权益领取" },
  { key: "activated_users", label: "关键行动" },
  { key: "path_buyer_users", label: "完整路径购买" },
];

const adjacentStages = [
  { key: "exposure_to_landing", label: "曝光→访问", from: "exposed_users", to: "landing_users" },
  { key: "landing_to_claim", label: "访问→领取", from: "landing_users", to: "claim_users" },
  { key: "claim_to_activation", label: "领取→行动", from: "claim_users", to: "activated_users" },
  { key: "activation_to_path_purchase", label: "行动→完整购买", from: "activated_users", to: "path_buyer_users" },
] as const;

/** Frontend fallback for historical v0.8 records created before path_diagnostics was persisted. */
export function derivePathBottlenecks(rows: FunnelRow[]): PathDiagnosticRow[] {
  return rows.flatMap((row) => {
    const candidates = adjacentStages.flatMap((stage, index) => {
      const before = Number(row[stage.from]);
      const after = Number(row[stage.to]);
      if (!Number.isFinite(before) || !Number.isFinite(after) || before <= 0 || after < 0 || after > before) return [];
      return [{
        variant: row.variant,
        stage_key: stage.key,
        stage_label: stage.label,
        from_users: before,
        to_users: after,
        continuation_rate: after / before,
        not_continued_users: before - after,
        stage_index: index,
      }];
    });
    if (!candidates.length || candidates.length !== adjacentStages.filter((stage) => Number(row[stage.from]) > 0).length) return [];
    const bottleneck = candidates.reduce((lowest, candidate) =>
      candidate.continuation_rate < lowest.continuation_rate ||
      (candidate.continuation_rate === lowest.continuation_rate && candidate.stage_index > lowest.stage_index)
        ? candidate : lowest,
    );
    const { stage_index: _stageIndex, ...result } = bottleneck;
    return [result];
  });
}

function FunnelPath({ row, strict }: { row: FunnelRow; strict: boolean }) {
  return (
    <div className="grid min-w-[690px] grid-cols-[6.5rem_repeat(4,minmax(5.75rem,1fr))_minmax(10rem,1.35fr)] items-stretch border-t border-border first:border-t-0">
      <div className="flex items-center px-4 py-4">
        <span className="truncate text-sm font-semibold">
          {row.variant ? variantName(row.variant) : "未分组"}
        </span>
      </div>
      {funnelSteps.map((step, index) => {
        const current = Number(step.key === "path_buyer_users" && !strict ? row.buyer_users : row[step.key] ?? 0);
        const exposureRate = index === 0 ? null : ratio(current, row.exposed_users);
        const isStrictPurchase = strict && step.key === "path_buyer_users";
        const pathRate = isStrictPurchase && row.activated_users > 0 && current <= row.activated_users
          ? row.activation_to_path_purchase_rate ?? current / row.activated_users
          : null;
        const missingPathPurchase = pathRate == null
          ? null
          : row.activated_without_path_purchase_users ?? row.activated_users - current;
        return (
          <div
            key={step.key}
            className="relative border-l border-border px-3 py-3.5"
          >
            <p className="text-[11px] text-muted-foreground">{step.label}</p>
            <p className="mt-1 text-sm font-semibold tabular-nums">
              {integer.format(current)}
            </p>
            <p className="mt-1 text-[11px] tabular-nums text-muted-foreground">
              {index === 0
                ? "起始样本"
                : pathRate == null
                  ? `相对曝光 ${percentOrDash(exposureRate)}`
                  : `行动承接 ${percentOrDash(pathRate)} · 未形成 ${integer.format(missingPathPurchase!)}`}
            </p>
            {index < funnelSteps.length - 1 && (
              <ArrowRight
                className="absolute -right-2 top-1/2 z-10 h-4 w-4 -translate-y-1/2 rounded-full bg-background p-0.5 text-muted-foreground"
                aria-hidden="true"
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function PathBottleneckSummary({ rows, evidenceId }: { rows: PathDiagnosticRow[]; evidenceId?: string }) {
  if (!rows.length) return null;
  return (
    <div className="mt-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold">最低承接节点</h4>
        {evidenceId && <span className="text-[11px] tabular-nums text-muted-foreground">路径证据 {evidenceId.slice(0, 12)}…</span>}
      </div>
      <div className="mt-2 divide-y divide-border border-y border-border">
        {rows.map((row) => (
          <div key={row.variant} className="grid grid-cols-[minmax(6.5rem,0.7fr)_minmax(10rem,1.3fr)_auto] items-center gap-4 py-3 text-sm">
            <span className="truncate font-medium" title={row.variant}>{variantName(row.variant)}</span>
            <span className="text-muted-foreground">{row.stage_label}</span>
            <span className="text-right tabular-nums">
              <strong className="font-semibold text-foreground">{percentOrDash(row.continuation_rate)}</strong>
              <span className="ml-2 text-xs text-muted-foreground">未进入下一步 {integer.format(row.not_continued_users)}</span>
            </span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">
        按每组相邻阶段承接率最低处定位。这是调查优先级，未进入下一步的人数不等于可恢复增量，也不证明原因。
      </p>
    </div>
  );
}

export function comparisonRows(message?: UIMessage): ComparisonRow[] | null {
  return parseRows(message)?.rows ?? null;
}

export function ReviewSnapshot({ message, diagnosis, diagnoses }: { message?: UIMessage; diagnosis?: UIMessage; diagnoses?: UIMessage[] }) {
  const data = parseRows(message);
  if (!data)
    return (
      <div className="flex min-h-full items-center justify-center px-6 py-16 text-center">
        <div className="max-w-sm">
          <p className="text-base font-medium">还没有可核对的复盘结果</p>
          <p className="mt-2 text-base leading-7 text-muted-foreground">
            确认活动口径后，系统会先完成确定性核算，再交给 Agent 解释。
          </p>
        </div>
      </div>
      );
  const pathBottlenecks = data.pathDiagnostics?.status === "evaluated" && data.pathDiagnostics.rows.length
    ? data.pathDiagnostics.rows
    : derivePathBottlenecks(data.funnel);
  const incrementalityBoundaryCopy = incrementalityBoundary(data.incrementality);
  const incrementalityIdentified = incrementalityBoundaryCopy.identified;
  const funnelEvidenceId = data.pathDiagnostics?.evidence_id ?? data.evidenceManifest.find((entry) => entry.kind === "funnel")?.evidence_id;

  const before = data.rows.find((row) => row.period === "baseline")!;
  const after = data.rows.find((row) => row.period === "activity")!;
  const equalDays = before.days === after.days;
  const legacyMetric = data.metricVersion !== "v0.8";
  const analysisEvidence = (kind: string) => data.analysisManifest.find((entry) => entry.kind === kind)?.evidence_id;
  const segmentEvidence = {
    variant: data.evidenceManifest.find((entry) => entry.kind === "funnel")?.evidence_id,
    channel: analysisEvidence("segment:channel"),
    location_id: analysisEvidence("segment:location_id"),
    audience: analysisEvidence("segment:audience"),
  };
  const dimensionMismatches = {
    variant: data.dataQuality?.buyers_without_matching_variant_exposure ?? 0,
    channel: data.dataQuality?.buyers_without_matching_channel_exposure ?? 0,
    location_id: data.dataQuality?.buyers_without_matching_location_exposure ?? 0,
    audience: data.dataQuality?.buyers_without_matching_audience_exposure ?? 0,
  };
  const costs = data.costs.reduce((totals, row) => {
    totals.set(
      row.variant,
      (totals.get(row.variant) ?? 0) + row.activity_cost_cents,
    );
    return totals;
  }, new Map<string, number>());
  const totalCost = data.costs.reduce(
    (sum, row) => sum + row.activity_cost_cents,
    0,
  );
  const costsUnallocated =
    data.costBoundary?.status === "shared_costs_unallocated";
  const contributionDelta = equalDays
    ? after.contribution_cents - before.contribution_cents
    : after.contribution_cents / after.days - before.contribution_cents / before.days;
  const targetEvaluated =
    data.target?.status === "evaluated" &&
    data.target.actual_value != null &&
    data.target.target_value != null;
  const targetAchieved = targetEvaluated
    ? data.target?.achieved === true
    : null;
  const verdict =
    targetAchieved === true
      ? contributionDelta < 0
        ? "主指标达成，但订单贡献额下降"
        : "主指标达成，订单贡献额同步改善"
      : targetAchieved === false
        ? contributionDelta < 0
          ? "主指标未达成，订单贡献额下降"
          : "主指标未达成，需继续定位路径缺口"
        : contributionDelta < 0
          ? "订单贡献额下降，目标状态待核对"
          : "经营结果已核算，目标状态待核对";

  return (
    <section
      aria-labelledby="review-overview"
      className="review-analysis mx-auto w-full max-w-[1040px] px-5 pb-14 pt-7 md:px-8 lg:px-10"
    >
      {legacyMetric && (
        <div className="mb-5 border-l-[3px] border-amber-600 bg-amber-50/65 px-4 py-3 text-sm leading-6 text-amber-950">
          这条历史记录使用旧版计算口径。时间切片与分组去重已更新；请在确认范围后重新核算，再用于经营决策。
        </div>
      )}
      <header className="border-b border-border pb-6">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="min-w-0 max-w-2xl">
            <h2
              id="review-overview"
              className="text-[1.35rem] font-semibold leading-[1.35] tracking-[-0.03em] text-foreground sm:text-[1.55rem]"
            >
              {verdict}
            </h2>
            <p className="mt-2 text-base leading-7 text-muted-foreground">
              活动期 {after.days} 天、对比期 {before.days} 天。该判断来自当前口径的确定性结果，不代表活动产生了因果增量。
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5 text-xs text-muted-foreground">
            {data.provenance && (
              <span className="rounded-full border border-border bg-background px-2 py-1 font-medium text-foreground">
                {data.provenance === "synthetic"
                  ? "演示数据 · 合成数据"
                  : "商家导入数据"}
              </span>
            )}
            <span className="inline-flex items-center gap-2">
              <ShieldCheck
                className="h-4 w-4 text-primary"
                aria-hidden="true"
              />
              本轮计算证据已绑定
            </span>
          </div>
        </div>

        <div className="mt-6 grid border-y border-border sm:grid-cols-3 sm:divide-x sm:divide-border">
          <div className="py-4 pr-4 sm:pr-5">
            <p className="text-xs font-medium text-muted-foreground">
              活动目标
            </p>
            {targetEvaluated ? (
              <>
                <p className="mt-2 text-base font-semibold tabular-nums">
                  {targetNames[data.target!.metric]}{" "}
                  {targetAchieved ? "已达到" : "未达到"}
                </p>
                <p className="mt-1 text-xs tabular-nums text-muted-foreground">
                  实际{" "}
                  {targetNumber(data.target!.actual_value!, data.target!.unit)}{" "}
                  / 目标{" "}
                  {targetNumber(data.target!.target_value!, data.target!.unit)}
                </p>
              </>
            ) : (
              <p className="mt-2 text-sm font-medium">尚未形成可计算目标</p>
            )}
          </div>
          <div className="border-t border-border py-4 sm:border-t-0 sm:px-5">
            <p className="text-xs font-medium text-muted-foreground">
              经营结果
            </p>
            <p
              className={`mt-2 text-base font-semibold ${contributionDelta < 0 ? "text-red-800" : contributionDelta > 0 ? "text-emerald-800" : "text-foreground"}`}
            >
              {equalDays ? "贡献额" : "日均贡献额"}
              {contributionDelta === 0
                ? "持平"
                : contributionDelta > 0
                  ? "增加"
                  : "减少"}{" "}
              {money(Math.abs(contributionDelta))}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              未扣除人力、租金等全部费用
            </p>
          </div>
          <div className="border-t border-border py-4 sm:border-t-0 sm:pl-5">
            <p className="text-xs font-medium text-muted-foreground">
              判断边界
            </p>
            <p className="mt-2 text-base font-semibold">
              {incrementalityBoundaryCopy.label}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {incrementalityBoundaryCopy.detail}
            </p>
          </div>
        </div>
      </header>

      {(diagnoses ?? (diagnosis ? [diagnosis] : [])).map((item) => <DiagnosticReadout key={item.id} diagnosis={item} dimensionMismatch={dimensionMismatches[item.payload.diagnostic_dimension as keyof typeof dimensionMismatches] ?? 0} />)}

      <TrendAnalysis rows={data.timeline} binDays={data.timelineBinDays} evidenceId={analysisEvidence("timeline")} comparable={equalDays} />

      {data.attributionTail && data.attributionDays != null && (
        <section className="border-b border-border py-7" aria-labelledby="tail-title">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h3 id="tail-title" className="text-base font-semibold">活动结束后的关联订单</h3>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                活动结束后 {data.attributionDays} 天内的订单，须能匹配活动期同一用户、同一方案/渠道/经营点/人群的先前曝光；单列，不并入活动期目标。
              </p>
            </div>
            {analysisEvidence("attribution_tail") && <span className="text-xs text-muted-foreground">证据 {analysisEvidence("attribution_tail")?.slice(0, 12)}…</span>}
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-4 border-y border-border py-4 sm:grid-cols-4">
            {[
              ["完成订单", integer.format(data.attributionTail.completed_orders)],
              ["关联购买用户", integer.format(data.attributionTail.buyer_users)],
              ["净收入", money(data.attributionTail.net_revenue_cents)],
              ["订单贡献额", money(data.attributionTail.contribution_cents)],
            ].map(([label, value]) => <div key={label}><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1.5 text-base font-semibold tabular-nums">{value}</dd></div>)}
          </dl>
          {data.attributionTail.candidate_orders != null && data.attributionTail.candidate_orders > 0 && (
            <p className="mt-3 text-xs leading-5 text-muted-foreground">
              窗口内完成订单 {integer.format(data.attributionTail.candidate_orders)} 笔；纳入 {integer.format(data.attributionTail.completed_orders)} 笔，
              未匹配同标签曝光 {integer.format(data.attributionTail.orders_without_matching_exposure ?? 0)} 笔，
              超出曝光后归因时长 {integer.format(data.attributionTail.orders_outside_exposure_window ?? 0)} 笔。
            </p>
          )}
          <p className="mt-3 text-xs leading-5 text-muted-foreground">只代表快照中已导入且满足规则的订单。未提供数据截至时间与对照组，不能断言尾窗已结算或活动带来增量。</p>
        </section>
      )}
      {data.attributionTailStatus === "requires_campaign_end" && (
        <p className="border-b border-border py-4 text-sm leading-6 text-muted-foreground">当前选择的是活动日期切片，尚未覆盖活动配置的结束日；不计算活动结束后的归因窗口，以免将活动中途的订单误称为尾效。</p>
      )}

      <section
        className="border-b border-border py-7"
        aria-labelledby="business-result-title"
      >
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h3 id="business-result-title" className="text-base font-semibold">
            主指标与经营结果
          </h3>
          <p className="text-xs text-muted-foreground">活动期 / 对比期</p>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-x-5 lg:grid-cols-4 lg:gap-x-7">
          <Metric
            label="完成订单"
            before={before.completed_orders}
            after={after.completed_orders}
            format={integer.format}
            comparable={equalDays}
          />
          <Metric
            label="净收入"
            before={before.net_revenue_cents}
            after={after.net_revenue_cents}
            format={money}
            comparable={equalDays}
          />
          <Metric
            label="商家优惠"
            before={before.merchant_discount_cents}
            after={after.merchant_discount_cents}
            format={money}
            comparable={equalDays}
          />
          <Metric
            label="订单贡献额"
            before={before.contribution_cents}
            after={after.contribution_cents}
            format={money}
            comparable={equalDays}
          />
        </dl>
      </section>

      <section className="border-b border-border py-7" aria-labelledby="unit-economics-title">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 id="unit-economics-title" className="text-base font-semibold">规模与单均质量</h3>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              区分订单规模变化与单笔经济性变化；这是算术拆解，不代表活动归因。
            </p>
          </div>
          <span className="text-xs text-muted-foreground">活动期 / 对比期</span>
        </div>
        <dl className="mt-4 grid gap-x-5 gap-y-5 border-y border-border py-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            {
              label: "日均订单",
              baseline: before.completed_orders / before.days,
              activity: after.completed_orders / after.days,
              format: (value: number) => value.toFixed(1),
            },
            {
              label: "订单净收入/单",
              baseline: before.completed_orders ? before.net_revenue_cents / before.completed_orders : null,
              activity: after.completed_orders ? after.net_revenue_cents / after.completed_orders : null,
              format: money,
            },
            {
              label: "商家优惠/单",
              baseline: before.completed_orders ? before.merchant_discount_cents / before.completed_orders : null,
              activity: after.completed_orders ? after.merchant_discount_cents / after.completed_orders : null,
              format: money,
            },
            {
              label: "订单贡献额/单",
              baseline: before.completed_orders ? before.contribution_cents / before.completed_orders : null,
              activity: after.completed_orders ? after.contribution_cents / after.completed_orders : null,
              format: money,
            },
          ].map((item) => (
            <div key={item.label}>
              <dt className="text-xs text-muted-foreground">{item.label}</dt>
              <dd className="mt-1.5 text-base font-semibold tabular-nums">
                {item.activity == null ? "—" : item.format(item.activity)}
              </dd>
              <p className="mt-1 text-xs tabular-nums text-muted-foreground">
                对比期 {item.baseline == null ? "—" : item.format(item.baseline)}
              </p>
            </div>
          ))}
        </dl>
        {(() => {
          const breakdown = decomposeDailyContribution(before, after);
          if (!breakdown) return <p className="mt-3 text-xs text-muted-foreground">有周期没有完成订单，单均贡献无法定义，因此不做规模与单均拆解。</p>;
          return <div className="mt-4 rounded-lg border border-border bg-muted/25 px-4 py-4">
            <p className="text-xs font-semibold">日均贡献额变化：{signed(breakdown.deltaCentsPerDay, money)} / 天</p>
            <div className="mt-2 grid grid-cols-2 gap-4 text-xs leading-5">
              <p>订单规模项 <strong className="block text-sm tabular-nums">{signed(breakdown.volumeCentsPerDay, money)} / 天</strong></p>
              <p>单均贡献项 <strong className="block text-sm tabular-nums">{signed(breakdown.unitCentsPerDay, money)} / 天</strong></p>
            </div>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">用两期均值对“日均订单 × 单均贡献”做对称恒等拆解；两项相加等于日均贡献额差值，不解释导致变化的原因。</p>
          </div>;
        })()}
        {!equalDays && (
          <p className="mt-3 text-xs leading-5 text-amber-800">
            两期天数不同，主指标的总量差仅供参考；优先比较日均订单与单均指标。
          </p>
        )}
      </section>

      {data.funnel.length > 0 && (
        <section
          className="border-b border-border py-7"
          aria-labelledby="path-title"
        >
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h3 id="path-title" className="text-base font-semibold">
                {legacyMetric ? "用户路径" : "顺序转化路径"}
              </h3>
              <p className="mt-1 text-base leading-7 text-muted-foreground">
                {legacyMetric
                  ? "旧版各节点独立去重，不能据此计算阶段流失。"
                  : "同一匿名用户按时间完成前序节点；末端是完整路径后购买，未走完整路径的购买者仍计入经营结果。"}
              </p>
            </div>
            <span className="text-xs text-muted-foreground">按实验组拆分</span>
          </div>
          <div className="mt-4 overflow-x-auto rounded-xl border border-border bg-background">
            {data.funnel.map((row) => (
              <FunnelPath key={row.variant} row={row} strict={!legacyMetric} />
            ))}
          </div>
          {!legacyMetric && <PathBottleneckSummary rows={pathBottlenecks} evidenceId={funnelEvidenceId} />}
        </section>
      )}

      <SegmentMatrix segments={data.segments} evidenceIds={segmentEvidence} scopeExposedUsers={data.dataQuality?.exposed_users} unlinkedBuyers={data.dataQuality?.buyers_without_prior_exposure} dimensionMismatches={dimensionMismatches} />

      <DataReliability quality={data.dataQuality} evidenceId={analysisEvidence("data_quality")}
        coverage={data.dataCoverage} coverageEvidenceId={analysisEvidence("data_coverage")} />

      <section
        className="grid gap-0 border-b border-border py-7 xl:grid-cols-[minmax(0,1.2fr)_minmax(17rem,0.8fr)] xl:divide-x xl:divide-border"
        aria-labelledby="experiment-title"
      >
        <div className="min-w-0 xl:pr-7">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <h3 id="experiment-title" className="text-base font-semibold">
              实验判断
            </h3>
            <span className="text-xs text-muted-foreground">
              双侧两比例 z 检验
            </span>
          </div>
          {data.experiment?.status === "observational_readout" &&
          data.experiment.comparisons.length > 0 ? (
            <div className="mt-4 divide-y divide-border border-y border-border">
              {data.experiment.comparisons.map((item) => {
                const [low, high] = item.ci95_difference_pp;
                const sampleIsSmall = item.sample_check !== "ok";
                const observed = item.descriptive_signal && !sampleIsSmall;
                return (
                  <div
                    key={`${item.control}-${item.treatment}`}
                    className="py-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold">
                          {variantName(item.treatment)} 对 {variantName(item.control)}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          转化率 {percent.format(item.treatment_rate)} /{" "}
                          {percent.format(item.control_rate)}
                        </p>
                      </div>
                      <span
                        className={`inline-flex items-center gap-1.5 text-xs font-medium ${observed ? "text-emerald-700" : "text-amber-800"}`}
                      >
                        {observed ? (
                          <Check className="h-3.5 w-3.5" />
                        ) : (
                          <TriangleAlert className="h-3.5 w-3.5" />
                        )}
                        {sampleIsSmall
                          ? "样本不足"
                          : observed
                            ? "观察到差异"
                            : "证据不足以确认差异"}
                      </span>
                    </div>
                    <div className="mt-4 grid grid-cols-3 gap-4 text-xs">
                      <div>
                        <p className="text-muted-foreground">差值</p>
                        <p className="mt-1 font-semibold tabular-nums">
                          {percentagePoints(item.difference_pp)} pp
                        </p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">95% 区间</p>
                        <p className="mt-1 font-semibold tabular-nums">
                          [{percentagePoints(low)}, {percentagePoints(high)}] pp
                        </p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">p 值</p>
                        <p className="mt-1 font-semibold tabular-nums">
                          {item.p_value.toFixed(3)}
                        </p>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="mt-4 text-base leading-7 text-muted-foreground">
              {data.experiment?.status === "insufficient_groups" ? "当前数据未形成可比较的实验组。" : "当前数据质量不足，已暂停组间检验。"}
            </p>
          )}
          <p className="mt-3 text-base leading-7 text-muted-foreground">
            {data.experiment?.assumption ?? "实验分流条件尚未验证。"}
          </p>
          {data.incrementalityReadiness && (
            <div className="mt-4 border-y border-border py-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs font-semibold">增量识别资格</p>
                <span className={`text-xs font-semibold ${data.incrementalityReadiness.status === "not_identified" ? "text-amber-800" : "text-emerald-700"}`}>
                  {data.incrementalityReadiness.status === "not_identified" ? "当前不可识别" : "设计条件已具备"}
                </span>
              </div>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                {data.incrementalityReadiness.detail}
              </p>
              {data.incrementalityReadiness.status === "not_identified" && (
                <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                  <div>
                    <dt className="text-muted-foreground">随机实验缺口</dt>
                    <dd className="mt-1 font-medium tabular-nums">
                      {data.incrementalityReadiness.randomized_experiment.missing.length} 项
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">DiD 缺口</dt>
                    <dd className="mt-1 font-medium tabular-nums">
                      {data.incrementalityReadiness.difference_in_differences.missing.length} 项
                    </dd>
                  </div>
                </dl>
              )}
            </div>
          )}
          {data.incrementality && data.incrementality.estimates.length > 0 && (
            <section className="mt-4" aria-labelledby="incrementality-title">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p id="incrementality-title" className="text-xs font-semibold">
                    同期对照增量
                  </p>
                  <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
                    平衡面板 DiD；估计对象是单个处理经营单元的周期均值，不是整场活动总增量。
                  </p>
                </div>
                <span
                  className={`inline-flex items-center gap-1.5 text-xs font-semibold ${
                    data.incrementality.status === "identified"
                      ? "text-emerald-700"
                      : "text-amber-800"
                  }`}
                >
                  {data.incrementality.status === "identified" ? (
                    <Check className="h-3.5 w-3.5" aria-hidden="true" />
                  ) : (
                    <TriangleAlert className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                  {data.incrementality.status === "identified"
                    ? "通过识别检验"
                    : "验证未通过"}
                </span>
              </div>
              <div className="mt-3 divide-y divide-border border-y border-border">
                {[...data.incrementality.estimates]
                  .sort(
                    (left, right) =>
                      incrementalityMetricOrder.indexOf(left.metric) -
                      incrementalityMetricOrder.indexOf(right.metric),
                  )
                  .map((estimate) => {
                  const ci = estimate.ci95;
                  return (
                    <article key={estimate.metric} className="py-4">
                      <div className="flex flex-wrap items-baseline justify-between gap-2">
                        <h4 className="text-sm font-semibold">
                          {incrementalityMetricNames[estimate.metric] ?? estimate.metric}
                        </h4>
                        <div className="text-right">
                          <p className="text-[11px] text-muted-foreground">
                            单个处理经营单元 ATT
                          </p>
                          <p className="mt-1 text-lg font-semibold tabular-nums tracking-tight">
                            {incrementalityValue(estimate.metric, estimate.estimate)}
                          </p>
                        </div>
                      </div>
                      <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-3 text-xs sm:grid-cols-4">
                        <div>
                          <dt className="text-muted-foreground">95% 区间</dt>
                          <dd className="mt-1 font-medium tabular-nums">
                            {ci
                              ? `[${incrementalityValue(estimate.metric, ci[0])}, ${incrementalityValue(estimate.metric, ci[1])}]`
                              : "—"}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-muted-foreground">显著性</dt>
                          <dd className="mt-1 font-medium tabular-nums">
                            p={probability(estimate.p_value)}
                            {estimate.relative_to_control_pre != null
                              ? ` · ${percent.format(estimate.relative_to_control_pre)} vs 对照前值`
                              : ""}
                          </dd>
                          {estimate.inference === "welch_t_small_sample" && estimate.degrees_of_freedom != null && (
                            <dd className="mt-1 text-[11px] text-muted-foreground">
                              Welch t · df={estimate.degrees_of_freedom.toFixed(1)}
                            </dd>
                          )}
                        </div>
                        <div>
                          <dt className="text-muted-foreground">经营单元</dt>
                          <dd className="mt-1 font-medium tabular-nums">
                            {estimate.treated_units ?? 0} 处理 / {estimate.control_units ?? 0} 对照
                          </dd>
                        </div>
                        <div>
                          <dt className="text-muted-foreground">观测周期</dt>
                          <dd className="mt-1 font-medium tabular-nums">
                            {estimate.pre_periods ?? 0} 前 / {estimate.post_periods ?? 0} 后
                          </dd>
                        </div>
                      </dl>
                      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs">
                        <span className={estimate.parallel_trends?.status === "passed" ? "text-emerald-700" : "text-amber-800"}>
                          前趋势 {estimate.parallel_trends?.status === "passed" ? "未见显著违背" : "未通过"}
                          {estimate.parallel_trends ? ` · p=${probability(estimate.parallel_trends.p_value)}` : ""}
                        </span>
                        <span className={estimate.placebo_test?.status === "passed" ? "text-emerald-700" : "text-amber-800"}>
                          安慰剂 {estimate.placebo_test?.status === "passed" ? "通过" : "未通过"}
                          {estimate.placebo_test ? ` · p=${probability(estimate.placebo_test.p_value)}` : ""}
                        </span>
                      </div>
                      {estimate.pretrend_warning && (
                        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                          {estimate.pretrend_warning}
                        </p>
                      )}
                      {(estimate.causal_boundary ?? estimate.detail) && (
                        <p className="mt-3 text-xs leading-5 text-muted-foreground">
                          {estimate.causal_boundary ?? estimate.detail}
                        </p>
                      )}
                    </article>
                  );
                })}
              </div>
            </section>
          )}
        </div>
        <div
          className="mt-7 min-w-0 border-t border-border pt-7 xl:ml-7 xl:mt-0 xl:border-t-0 xl:pt-0"
          aria-labelledby="cost-title"
        >
          <div className="flex items-end justify-between gap-3">
            <h3 id="cost-title" className="text-base font-semibold">
              成本与价值
            </h3>
            <span className="text-xs tabular-nums text-muted-foreground">
              {costsUnallocated
                ? `共享费用 ${money(data.costBoundary?.shared_cost_cents ?? totalCost)}`
                : `合计 ${money(totalCost)}`}
            </span>
          </div>
          <div className="mt-4 divide-y divide-border border-y border-border">
            {data.funnel.map((row) => {
              const cost = costs.get(row.variant) ?? 0;
              return (
                <div
                  key={row.variant}
                  className="grid grid-cols-[1fr_auto] gap-x-4 py-3.5 text-sm"
                >
                  <p className="font-medium">
                    {row.variant ? variantName(row.variant) : "未分组"}
                  </p>
                  <p className="font-semibold tabular-nums">
                    {!costsUnallocated && cost > 0
                      ? `${(row.contribution_cents / cost).toFixed(2)}×`
                      : "—"}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {costsUnallocated
                      ? "当前筛选范围费用待分摊"
                      : `活动成本 ${money(cost)}`}
                  </p>
                  <p className="mt-1 text-right text-xs text-muted-foreground">
                    贡献/成本
                  </p>
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-base leading-7 text-muted-foreground">
            {costsUnallocated
              ? data.costBoundary?.detail
              : "贡献/成本是观察性投入效率，不等于财务 ROI。"}
          </p>
        </div>
      </section>

      <details className="group border-b border-border py-6">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-primary">
          <div>
            <h3 className="text-base font-semibold">证据与复盘口径</h3>
            <p className="mt-1 text-base leading-7 text-muted-foreground">
              展开核对本轮范围、快照指纹、查询证据与计算参数。
            </p>
          </div>
          <span className="shrink-0 text-xs font-medium text-primary group-open:hidden">
            展开
          </span>
          <span className="hidden shrink-0 text-xs font-medium text-primary group-open:inline">
            收起
          </span>
        </summary>

        <div className="mt-5 border-t border-border pt-5">
          <dl className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-medium text-muted-foreground">
                活动期
              </dt>
              <dd className="mt-1 text-sm font-medium tabular-nums">
                {data.scope
                  ? `${data.scope.activity.start} — ${data.scope.activity.end}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground">
                对比期
              </dt>
              <dd className="mt-1 text-sm font-medium tabular-nums">
                {data.scope
                  ? `${data.scope.baseline.start} — ${data.scope.baseline.end}`
                  : "—"}
              </dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-xs font-medium text-muted-foreground">
                筛选范围
              </dt>
              <dd className="mt-1 text-sm font-medium">
                {filterSummary(data.scope)}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground">
                Scope ID
              </dt>
              <dd className="mt-1 break-all font-mono text-xs">
                {data.scopeId ?? "—"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground">
                快照 SHA-256
              </dt>
              <dd className="mt-1 break-all font-mono text-xs">
                {data.snapshotId ?? "—"}
              </dd>
            </div>
          </dl>

          {(data.evidenceManifest.length > 0 ||
            data.relatedEvidenceIds.length > 0) && (
            <div className="mt-5">
              <p className="text-xs font-medium text-muted-foreground">
                关联证据
              </p>
              <ul className="mt-2 grid gap-2 sm:grid-cols-2">
                {(data.evidenceManifest.length > 0
                  ? data.evidenceManifest
                  : [
                      String(message?.payload.evidence_id ?? ""),
                      ...data.relatedEvidenceIds,
                    ]
                      .filter(Boolean)
                      .map(
                        (evidence_id): EvidenceManifestEntry => ({
                          kind: "outcome" as const,
                          evidence_id,
                        }),
                      )
                ).map((entry) => (
                  <li
                    key={entry.evidence_id}
                    className="rounded-md border border-border bg-background px-3 py-2"
                  >
                    <span className="block text-xs font-medium text-foreground">
                      {evidenceKindNames[entry.kind] ?? entry.kind}
                    </span>
                    <span className="mt-1 block break-all font-mono text-[11px] text-muted-foreground">
                      {entry.evidence_id}
                    </span>
                    {(entry.row_count != null || entry.elapsed_ms != null) && (
                      <span className="mt-1 block text-[11px] text-muted-foreground">
                        {entry.row_count ?? "—"} 行 · {entry.elapsed_ms ?? "—"}{" "}
                        ms ·{entry.truncated === false ? " 完整" : " 待核对"}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.warnings.length > 0 && (
            <div className="mt-5 border-l-2 border-amber-500 pl-4">
              <p className="text-xs font-medium text-muted-foreground">
                计算边界
              </p>
              <ul className="mt-2 space-y-1 text-base leading-7 text-foreground">
                {data.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}

          {(data.sql || data.parameters) && (
            <details className="mt-5 border-t border-border pt-4">
              <summary className="cursor-pointer text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-primary">
                查看查询与绑定参数
              </summary>
              <div className="mt-3 space-y-3">
                {data.sql && (
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs leading-5">
                    {data.sql}
                  </pre>
                )}
                {data.parameters && (
                  <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs leading-5">
                    {JSON.stringify(data.parameters, null, 2)}
                  </pre>
                )}
              </div>
            </details>
          )}

          <p className="mt-4 text-xs text-muted-foreground">
            查询耗时 {data.elapsedMs ?? "—"} ms · 结果
            {data.truncated === false ? "完整" : "状态待核对"}
          </p>
        </div>
      </details>

      <footer className="flex flex-wrap items-start justify-between gap-3 pt-5 text-base leading-7 text-muted-foreground">
        <p className="max-w-2xl">
          口径：退款后净收入；购买仅统计完成订单。
          {incrementalityIdentified
            ? "前后变化仍是描述性证据；上方增量仅在同期对照、稳定构成、平行趋势与无干扰假设下成立。"
            : "前后周期变化不能单独证明活动增量。"}
        </p>
        <p className="tabular-nums">
          指标版本 {String(message?.payload.metric_version ?? "—")}
        </p>
      </footer>
    </section>
  );
}
