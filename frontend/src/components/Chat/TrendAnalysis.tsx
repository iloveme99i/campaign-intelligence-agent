import { useState } from "react";

export type TrendRow = {
  period: "baseline" | "activity";
  bin_index: number;
  bin_start: string;
  bin_end: string;
  completed_orders: number;
  net_revenue_cents: number;
  contribution_cents: number;
  merchant_discount_cents: number;
  refund_cents: number;
};

type MetricKey = "contribution_cents" | "completed_orders" | "net_revenue_cents";
const metrics: Array<{ key: MetricKey; label: string }> = [
  { key: "contribution_cents", label: "订单贡献额" },
  { key: "completed_orders", label: "完成订单" },
  { key: "net_revenue_cents", label: "净收入" },
];
const integer = new Intl.NumberFormat("zh-CN");
const currency = new Intl.NumberFormat("zh-CN", {
  style: "currency", currency: "CNY", maximumFractionDigits: 0,
});
function format(value: number, key: MetricKey) {
  return key === "completed_orders" ? integer.format(value) : currency.format(value / 100);
}

function validRows(rows: TrendRow[]) {
  return rows.filter((row) =>
    row && ["baseline", "activity"].includes(row.period) &&
    Number.isInteger(row.bin_index) && row.bin_index >= 0 &&
    [row.completed_orders, row.net_revenue_cents, row.contribution_cents].every(Number.isFinite),
  );
}

export function TrendAnalysis({ rows, binDays, evidenceId, comparable = true }: {
  rows: TrendRow[];
  binDays: number;
  evidenceId?: string;
  comparable?: boolean;
}) {
  const [metric, setMetric] = useState<MetricKey>("contribution_cents");
  const valid = validRows(rows);
  if (!valid.length) return null;
  const before = valid.filter((row) => row.period === "baseline");
  const after = valid.filter((row) => row.period === "activity");
  const maxIndex = Math.max(1, ...valid.map((row) => row.bin_index));
  const values = valid.map((row) => row[metric]);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const span = Math.max(1, high - low);
  const x = (index: number) => 42 + (index / maxIndex) * 564;
  const y = (value: number) => 24 + ((high - value) / span) * 156;
  const points = (series: TrendRow[]) =>
    series.map((row) => `${x(row.bin_index)},${y(row[metric])}`).join(" ");
  const paired = after
    .map((row) => ({ row, baseline: before.find((item) => item.bin_index === row.bin_index) }))
    .filter((item): item is { row: TrendRow; baseline: TrendRow } => Boolean(item.baseline));
  const weakest = comparable && paired.length > 0
    ? paired.reduce((current, item) =>
      item.row[metric] - item.baseline[metric] < current.row[metric] - current.baseline[metric]
        ? item : current,
    )
    : null;

  return (
    <section className="border-b border-border py-7" aria-labelledby="trend-analysis-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="trend-analysis-title" className="text-base font-semibold">活动期走势</h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            以周期内第 {binDays === 1 ? "N 天" : "N 周"} 对齐两期；观察波动，不据此认定活动效果。
          </p>
          {!comparable && <p className="mt-1 text-xs leading-5 text-amber-800">两期天数不同，仅展示走势，不比较同序位差值。</p>}
        </div>
        {evidenceId && <span className="text-[11px] tabular-nums text-muted-foreground">时间证据 {evidenceId}</span>}
      </div>
      <div className="mt-4 flex flex-wrap gap-1.5" role="group" aria-label="走势指标">
        {metrics.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={metric === item.key}
            onClick={() => setMetric(item.key)}
            className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${metric === item.key ? "border-primary bg-primary/10 text-primary" : "border-border bg-background text-muted-foreground hover:text-foreground"}`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="mt-4 rounded-xl border border-border bg-background px-3 pb-3 pt-2">
        <div className="flex items-center justify-between gap-4 px-2 py-1.5 text-xs text-muted-foreground">
          <span>{format(high, metric)}</span>
          <div className="flex items-center gap-4">
            <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-4 bg-primary" />活动期</span>
            <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-4 bg-slate-400" />对比期</span>
          </div>
        </div>
        <svg
          className="h-[185px] w-full"
          viewBox="0 0 648 204"
          role="img"
          aria-label={`${metrics.find((item) => item.key === metric)?.label}的活动期与对比期走势`}
          preserveAspectRatio="none"
        >
          {[0, 0.5, 1].map((fraction) => (
            <line key={fraction} x1="42" x2="606" y1={24 + fraction * 156} y2={24 + fraction * 156} stroke="currentColor" className="text-border" strokeWidth="1" />
          ))}
          {low < 0 && <line x1="42" x2="606" y1={y(0)} y2={y(0)} stroke="currentColor" className="text-muted-foreground" strokeDasharray="4 4" />}
          {before.length > 1 && <polyline points={points(before)} fill="none" stroke="#94a3b8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
          {after.length > 1 && <polyline points={points(after)} fill="none" stroke="#315de7" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
          {after.length === 1 && <circle cx={x(after[0].bin_index)} cy={y(after[0][metric])} r="4" fill="#315de7" />}
          {before.length === 1 && <circle cx={x(before[0].bin_index)} cy={y(before[0][metric])} r="4" fill="#94a3b8" />}
          <text x="42" y="200" fill="currentColor" className="text-muted-foreground" fontSize="11">第 1 {binDays === 1 ? "天" : "周"}</text>
          <text x="606" y="200" fill="currentColor" className="text-muted-foreground" fontSize="11" textAnchor="end">第 {maxIndex + 1} {binDays === 1 ? "天" : "周"}</text>
        </svg>
      </div>
      {weakest && (
        <p className="mt-3 text-sm leading-6">
          相同序位中，第 {weakest.row.bin_index + 1} {binDays === 1 ? "天" : "周"}的{metrics.find((item) => item.key === metric)?.label}差值最低：
          <span className="font-semibold tabular-nums">{format(weakest.row[metric] - weakest.baseline[metric], metric)}</span>。
          <span className="text-muted-foreground"> 需结合当天流量、库存或执行记录解释。</span>
        </p>
      )}
      <details className="mt-4 border-t border-border pt-3 text-sm">
        <summary className="cursor-pointer font-medium outline-none focus-visible:ring-2 focus-visible:ring-primary">核对逐期明细</summary>
        <div className="mt-3 max-h-72 overflow-auto rounded-lg border border-border">
          <table className="w-full min-w-[470px] text-left text-xs">
            <thead className="sticky top-0 bg-muted text-muted-foreground"><tr>
              <th scope="col" className="px-3 py-2 font-medium">周期</th>
              <th scope="col" className="px-3 py-2 font-medium">日期</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">完成订单</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">净收入</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">贡献额</th>
            </tr></thead>
            <tbody className="divide-y divide-border">{valid.map((row) => (
              <tr key={`${row.period}-${row.bin_index}`}>
                <td className="px-3 py-2">{row.period === "activity" ? "活动期" : "对比期"}</td>
                <td className="px-3 py-2 tabular-nums">{row.bin_start}{row.bin_end !== row.bin_start ? ` — ${row.bin_end}` : ""}</td>
                <td className="px-3 py-2 text-right tabular-nums">{integer.format(row.completed_orders)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{currency.format(row.net_revenue_cents / 100)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{currency.format(row.contribution_cents / 100)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
