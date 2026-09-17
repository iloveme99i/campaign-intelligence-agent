import { useState } from "react";
import { merchantName } from "@/lib/merchantNames";

type Dimension = "variant" | "channel" | "location_id" | "audience";
export type SegmentRow = {
  dimension_value: string;
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
  order_contribution_cents: number;
  conversion_rate: number | null;
  total_groups?: number;
};
export type SegmentResults = Partial<Record<Dimension, SegmentRow[]>>;
const dimensions: Array<{ key: Dimension; label: string }> = [
  { key: "variant", label: "实验组" },
  { key: "channel", label: "渠道" },
  { key: "location_id", label: "经营点" },
  { key: "audience", label: "人群" },
];
const integer = new Intl.NumberFormat("zh-CN");
const percent = new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 1 });
const currency = new Intl.NumberFormat("zh-CN", {
  style: "currency", currency: "CNY", maximumFractionDigits: 2,
});
const ratio = (part: number, whole: number) => whole > 0 ? percent.format(part / whole) : "—";

export function SegmentMatrix({ segments, evidenceIds, scopeExposedUsers, unlinkedBuyers = 0, dimensionMismatches }: {
  segments: SegmentResults;
  evidenceIds: Partial<Record<Dimension, string>>;
  scopeExposedUsers?: number;
  unlinkedBuyers?: number;
  dimensionMismatches?: Partial<Record<Dimension, number>>;
}) {
  const [dimension, setDimension] = useState<Dimension>("channel");
  if (!Object.values(segments).some((rows) => Array.isArray(rows) && rows.length)) return null;
  const rows = (segments[dimension] ?? []).filter((row) =>
    row && typeof row.dimension_value === "string" &&
    [row.exposed_users, row.buyer_users, row.order_contribution_cents].every(Number.isFinite),
  );
  const substantial = rows.filter((row) => row.exposed_users >= 30);
  const rates = substantial.map((row) => row.exposed_users > 0 ? row.buyer_users / row.exposed_users : 0);
  const dimensionMismatch = dimensionMismatches?.[dimension] ?? 0;
  const range = unlinkedBuyers === 0 && dimensionMismatch === 0 && rates.length >= 2 ? `${percent.format(Math.min(...rates))}—${percent.format(Math.max(...rates))}` : null;
  const totalGroups = rows[0]?.total_groups ?? rows.length;
  const duplicateReach = scopeExposedUsers != null && rows.reduce((sum, row) => sum + row.exposed_users, 0) > scopeExposedUsers;

  return (
    <section className="border-b border-border py-7" aria-labelledby="segment-matrix-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id="segment-matrix-title" className="text-base font-semibold">分层表现</h3>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">同时审阅触达对象与购买价值；切换维度不会改变已确认复盘口径。</p>
        </div>
        <span className="text-xs text-muted-foreground">{totalGroups} 个{dimensions.find((item) => item.key === dimension)?.label}分组</span>
      </div>
      <div className="mt-4 flex gap-1 overflow-x-auto border-b border-border" role="group" aria-label="分层维度">
        {dimensions.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={dimension === item.key}
            onClick={() => setDimension(item.key)}
            className={`shrink-0 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${dimension === item.key ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          >{item.label}</button>
        ))}
      </div>
      {rows.length ? (
        <>
          <div className="mt-4 flex flex-wrap items-baseline gap-x-5 gap-y-1 text-xs text-muted-foreground">
            <span>展示 {rows.length}{totalGroups > rows.length ? ` / ${totalGroups}` : ""} 组，按曝光人数排序</span>
            {range && <span>至少 30 名曝光用户的组别，购买/曝光范围 <strong className="font-semibold tabular-nums text-foreground">{range}</strong></span>}
            {evidenceIds[dimension] && <span title={evidenceIds[dimension]}>查询证据 {evidenceIds[dimension]?.slice(0, 12)}…</span>}
          </div>
          <div className="mt-3 max-h-[430px] overflow-auto rounded-xl border border-border bg-background">
            <table className="w-full min-w-[860px] border-collapse text-left text-sm">
              <thead className="sticky top-0 z-10 bg-muted text-xs text-muted-foreground"><tr>
                <th scope="col" className="px-4 py-3 font-medium">{dimensions.find((item) => item.key === dimension)?.label}</th>
                <th scope="col" className="px-3 py-3 text-right font-medium">曝光</th>
                <th scope="col" className="px-3 py-3 text-right font-medium">领取/曝光</th>
                <th scope="col" className="px-3 py-3 text-right font-medium">行动/曝光</th>
                <th scope="col" className="px-3 py-3 text-right font-medium">行动→完整购买</th>
                <th scope="col" className="px-3 py-3 text-right font-medium">购买用户</th>
                <th scope="col" className="px-3 py-3 text-right font-medium">购买/曝光</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">贡献额/曝光</th>
              </tr></thead>
              <tbody className="divide-y divide-border">{rows.map((row) => (
                <tr key={row.dimension_value} className="hover:bg-muted/35">
                  <th scope="row" className="max-w-[170px] truncate px-4 py-3.5 font-medium" title={row.dimension_value}>
                    {row.dimension_value ? merchantName(row.dimension_value) : "未标注"}
                  </th>
                  <td className="px-3 py-3.5 text-right tabular-nums">{integer.format(row.exposed_users)}</td>
                  <td className="px-3 py-3.5 text-right tabular-nums">{ratio(row.claim_users, row.exposed_users)}</td>
                  <td className="px-3 py-3.5 text-right tabular-nums">{ratio(row.activated_users, row.exposed_users)}</td>
                  <td className="px-3 py-3.5 text-right tabular-nums">
                    {row.path_buyer_users == null || row.activated_users <= 0 ? "—" : row.path_buyer_users > row.activated_users ? "待核对" : (
                      <>
                        <span className="block font-semibold">{percent.format(row.activation_to_path_purchase_rate ?? row.path_buyer_users / row.activated_users)}</span>
                        <span className="mt-0.5 block text-[11px] text-muted-foreground">未形成 {integer.format(row.activated_without_path_purchase_users ?? row.activated_users - row.path_buyer_users)}</span>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-3.5 text-right tabular-nums">{integer.format(row.buyer_users)}</td>
                  <td className="px-3 py-3.5 text-right font-semibold tabular-nums">{unlinkedBuyers > 0 || dimensionMismatch > 0 || row.buyer_users > row.exposed_users ? "待核对" : ratio(row.buyer_users, row.exposed_users)}</td>
                  <td className="px-4 py-3.5 text-right tabular-nums">{row.exposed_users > 0 ? currency.format(row.order_contribution_cents / row.exposed_users / 100) : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            {duplicateReach ? "存在跨组曝光，分组人数不可相加成全活动去重人数。" : "组别读数为描述性差异，不代表方案优劣。"}
            {rows.some((row) => row.path_buyer_users != null) && " “未形成”是本期已行动但未完成严格购买路径的用户，不等于永久流失。"}
            {rows.some((row) => row.exposed_users < 30) && " 少于 30 名曝光用户的组别不纳入范围比较。"}
            {unlinkedBuyers > 0 && " 购买用户存在未链接先前曝光的情况，购买/曝光率暂不展示。"}
            {dimensionMismatch > 0 && ` ${integer.format(dimensionMismatch)} 名购买用户的订单${dimensions.find((item) => item.key === dimension)?.label}标签与先前曝光不一致；该维度购买/曝光率暂不展示。`}
            {totalGroups > rows.length && " 当前只展示曝光量最高的 50 组。"}
          </p>
        </>
      ) : <p className="py-8 text-sm text-muted-foreground">当前范围没有该维度的可比较分组。</p>}
    </section>
  );
}
