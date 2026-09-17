import { TriangleAlert } from "lucide-react";

export type QualityReadout = {
  exposed_users: number;
  buyer_users: number;
  cross_variant_exposed_users: number;
  buyers_without_prior_exposure: number;
  buyers_without_matching_variant_exposure?: number;
  buyers_without_matching_channel_exposure?: number;
  buyers_without_matching_location_exposure?: number;
  buyers_without_matching_audience_exposure?: number;
};
export type CoverageReadout = {
  baseline_days: number;
  activity_days: number;
  baseline_days_with_orders: number;
  activity_days_with_orders: number;
  activity_days_with_exposure: number;
  last_observed_activity_order_date: string | null;
};
const integer = new Intl.NumberFormat("zh-CN");
const percent = new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 1 });

export function DataReliability({ quality, evidenceId, coverage, coverageEvidenceId }: {
  quality?: QualityReadout;
  evidenceId?: string;
  coverage?: CoverageReadout;
  coverageEvidenceId?: string;
}) {
  if (!quality || ![
    quality.exposed_users,
    quality.buyer_users,
    quality.cross_variant_exposed_users,
    quality.buyers_without_prior_exposure,
  ].every(Number.isFinite)) return null;
  const linked = Math.max(0, quality.buyer_users - quality.buyers_without_prior_exposure);
  const linkRate = quality.buyer_users > 0 ? linked / quality.buyer_users : null;
  const mismatches = [
    ["实验组", quality.buyers_without_matching_variant_exposure],
    ["渠道", quality.buyers_without_matching_channel_exposure],
    ["经营点", quality.buyers_without_matching_location_exposure],
    ["人群", quality.buyers_without_matching_audience_exposure],
  ].filter((item): item is [string, number] => typeof item[1] === "number" && item[1] > 0);
  const hasIssue = quality.buyers_without_prior_exposure > 0 || quality.cross_variant_exposed_users > 0 || mismatches.length > 0;
  const validCoverage = coverage && [
    coverage.baseline_days, coverage.activity_days, coverage.baseline_days_with_orders,
    coverage.activity_days_with_orders, coverage.activity_days_with_exposure,
  ].every((value) => Number.isInteger(value) && value >= 0);
  const coverageGap = validCoverage && (
    coverage.baseline_days_with_orders < coverage.baseline_days ||
    coverage.activity_days_with_orders < coverage.activity_days ||
    coverage.activity_days_with_exposure < coverage.activity_days
  );
  return (
    <section className="border-b border-border py-7" aria-labelledby="data-reliability-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="data-reliability-title" className="text-base font-semibold">数据可信边界</h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">先检查行为与交易能否串起来，再讨论路径或方案差异。</p>
        </div>
        {evidenceId && <span title={evidenceId} className="text-[11px] text-muted-foreground">校验依据 {evidenceId.slice(0, 12)}…</span>}
      </div>
      <dl className="mt-4 grid gap-x-6 gap-y-4 border-y border-border py-4 sm:grid-cols-3">
        <div><dt className="text-xs text-muted-foreground">可链接到先前曝光的购买用户</dt>
          <dd className="mt-1.5 text-base font-semibold tabular-nums">{integer.format(linked)} / {integer.format(quality.buyer_users)}</dd>
          <p className="mt-1 text-xs text-muted-foreground">{linkRate == null ? "无购买用户" : `${percent.format(linkRate)} 可链接`}</p>
        </div>
        <div><dt className="text-xs text-muted-foreground">无法链接先前曝光</dt>
          <dd className="mt-1.5 text-base font-semibold tabular-nums">{integer.format(quality.buyers_without_prior_exposure)} 人</dd>
          <p className="mt-1 text-xs text-muted-foreground">不应强行计入完整路径</p>
        </div>
        <div><dt className="text-xs text-muted-foreground">跨实验组曝光</dt>
          <dd className="mt-1.5 text-base font-semibold tabular-nums">{integer.format(quality.cross_variant_exposed_users)} 人</dd>
          <p className="mt-1 text-xs text-muted-foreground">会污染组间比较</p>
        </div>
      </dl>
      {mismatches.length > 0 && <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-amber-900">
        <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>订单分组与先前曝光不一致：{mismatches.map(([label, count]) => `${label} ${integer.format(count)} 人`).join("、")}。相应维度的购买/曝光率及实验测算已暂停；订单量与贡献额仍可核对。</span>
      </p>}
      <div className="mt-5 border-t border-border pt-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h4 className="text-sm font-semibold">日期覆盖核对</h4>
          {coverageEvidenceId && <span title={coverageEvidenceId} className="text-[11px] text-muted-foreground">查询证据 {coverageEvidenceId.slice(0, 12)}…</span>}
        </div>
        {validCoverage ? <>
          <dl className="mt-3 grid grid-cols-3 gap-3 text-xs">
            <div><dt className="leading-5 text-muted-foreground">对比期有订单</dt><dd className="mt-1 text-sm font-semibold tabular-nums">{coverage.baseline_days_with_orders}/{coverage.baseline_days} 天</dd></div>
            <div><dt className="leading-5 text-muted-foreground">活动期有订单</dt><dd className="mt-1 text-sm font-semibold tabular-nums">{coverage.activity_days_with_orders}/{coverage.activity_days} 天</dd></div>
            <div><dt className="leading-5 text-muted-foreground">活动期有曝光</dt><dd className="mt-1 text-sm font-semibold tabular-nums">{coverage.activity_days_with_exposure}/{coverage.activity_days} 天</dd></div>
          </dl>
          <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-muted-foreground">
            {coverageGap && <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700" aria-hidden="true" />}
            <span>{coverageGap
              ? "至少一个日期没有对应记录。它可能是真实零单/零曝光，也可能是导出漏行；请核对订单与埋点导出范围，再采用趋势判断。"
              : "每个日期均观察到相应记录，但这仍不能证明导出完整；需向数据提供方核对截止时间与缺失情况。"}</span>
          </p>
          {coverage.last_observed_activity_order_date && <p className="mt-1 text-xs text-muted-foreground">最后观察到活动订单：{coverage.last_observed_activity_order_date}。这不是数据导出截止时间。</p>}
        </> : <p className="mt-2 text-xs leading-5 text-amber-800">该记录没有日期覆盖证据；重新核算后才能检查有记录的日数，不能据此认定导出完整。</p>}
      </div>
      <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-muted-foreground">
        {hasIssue && <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700" aria-hidden="true" />}
        <span>
          {hasIssue
            ? "数据存在路径、分组标签或跨组触达问题；请先核查埋点、订单归属和实验分配，再根据组间差异调整资源。"
            : "当前范围未发现上述路径或分组标签问题，但 CSV 仍无法验证随机分流、并行活动干扰和身份拼接完整性。"}
        </span>
      </p>
    </section>
  );
}
