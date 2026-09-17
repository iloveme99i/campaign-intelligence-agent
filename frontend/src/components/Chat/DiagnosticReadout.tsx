import { ArrowUpRight, Info } from "lucide-react";
import type { UIMessage } from "@/types";
import { merchantName } from "@/lib/merchantNames";

type Row = {
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
};

const dimensionNames: Record<string, string> = {
  variant: "实验组",
  channel: "渠道",
  location_id: "经营点",
  audience: "人群",
};
const integer = new Intl.NumberFormat("zh-CN");
const percent = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  maximumFractionDigits: 1,
});
const money = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  maximumFractionDigits: 0,
});
const rate = (part: number, total: number) =>
  total > 0 ? percent.format(part / total) : "—";

function pathConversion(row: Row) {
  if (row.path_buyer_users == null || row.activated_users <= 0 || row.path_buyer_users > row.activated_users) return null;
  const conversion = row.activation_to_path_purchase_rate ?? row.path_buyer_users / row.activated_users;
  const notPurchased = row.activated_without_path_purchase_users ?? row.activated_users - row.path_buyer_users;
  return { conversion, notPurchased };
}

function readableRows(message?: UIMessage): Row[] {
  if (!Array.isArray(message?.payload.rows)) return [];
  return message.payload.rows.filter((row): row is Row => {
    if (!row || typeof row !== "object") return false;
    const value = row as Partial<Row>;
    return (
      typeof value.dimension_value === "string" &&
      [
        value.exposed_users,
        value.landing_users,
        value.claim_users,
        value.activated_users,
        value.buyer_users,
        value.completed_orders,
        value.net_revenue_cents,
        value.order_contribution_cents,
      ].every((number) => typeof number === "number" && Number.isFinite(number))
    );
  });
}

export function DiagnosticReadout({ diagnosis, dimensionMismatch = 0 }: { diagnosis?: UIMessage; dimensionMismatch?: number }) {
  if (!diagnosis) return null;
  const rows = readableRows(diagnosis);
  const dimension =
    typeof diagnosis.payload.diagnostic_dimension === "string"
      ? diagnosis.payload.diagnostic_dimension
      : "variant";
  const label = dimensionNames[dimension] ?? "维度";
  const evidenceId =
    typeof diagnosis.payload.evidence_id === "string"
      ? diagnosis.payload.evidence_id
      : null;
  const hasPathMismatch = rows.some((row) =>
    row.landing_users > row.exposed_users ||
    row.claim_users > row.landing_users ||
    row.activated_users > row.claim_users ||
    (row.path_buyer_users != null && row.path_buyer_users > row.activated_users) ||
    row.buyer_users > row.exposed_users,
  );

  return (
    <section className="border-b border-border py-7" aria-labelledby="diagnostic-readout-title">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-muted-foreground">本轮定向下钻</p>
          <h3 id="diagnostic-readout-title" className="mt-1 text-base font-semibold">
            {label}差异
          </h3>
        </div>
        {evidenceId && (
          <span className="text-[11px] tabular-nums text-muted-foreground">
            诊断证据 {evidenceId}
          </span>
        )}
      </div>
      {rows.length ? (
        <>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            同一活动期、同一筛选口径下比较分组路径与订单贡献额；这里是观察读数，不是增量或因果归因。
          </p>
          <div className="mt-4 overflow-x-auto rounded-xl border border-border bg-background">
            <table className="w-full min-w-[840px] border-collapse text-left text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">{label}</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">曝光用户</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">访问/曝光</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">领取/曝光</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">行动/曝光</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">购买/曝光</th>
                  <th scope="col" className="px-3 py-3 text-right font-medium">行动→完整购买</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">订单贡献额</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => {
                  const path = pathConversion(row);
                  return <tr key={row.dimension_value} className="transition-colors hover:bg-muted/35">
                    <th scope="row" className="px-4 py-3.5 font-semibold">
                      {row.dimension_value ? merchantName(row.dimension_value) : "未标注"}
                    </th>
                    <td className="px-3 py-3.5 text-right tabular-nums">{integer.format(row.exposed_users)}</td>
                    <td className="px-3 py-3.5 text-right tabular-nums">{rate(row.landing_users, row.exposed_users)}</td>
                    <td className="px-3 py-3.5 text-right tabular-nums">{rate(row.claim_users, row.exposed_users)}</td>
                    <td className="px-3 py-3.5 text-right tabular-nums">{rate(row.activated_users, row.exposed_users)}</td>
                    <td className="px-3 py-3.5 text-right font-semibold tabular-nums">{dimensionMismatch > 0 || hasPathMismatch ? "待核对" : rate(row.buyer_users, row.exposed_users)}</td>
                    <td className="px-3 py-3.5 text-right tabular-nums">
                      {path ? (
                        <>
                          <span className="block font-semibold">{percent.format(path.conversion)}</span>
                          <span className="mt-0.5 block text-[11px] text-muted-foreground">完整购买 {integer.format(row.path_buyer_users!)} · 未形成 {integer.format(path.notPurchased)}</span>
                        </>
                      ) : row.path_buyer_users != null && row.path_buyer_users > row.activated_users ? "待核对" : "—"}
                    </td>
                    <td className="px-4 py-3.5 text-right font-semibold tabular-nums">{money.format(row.order_contribution_cents / 100)}</td>
                  </tr>;
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-start gap-2 text-xs leading-5 text-muted-foreground">
            {hasPathMismatch || dimensionMismatch > 0 ? <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-700" /> : <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
            <p>
              {dimensionMismatch > 0
                ? `${integer.format(dimensionMismatch)} 名购买用户的订单${label}标签与先前曝光不一致；该维度的购买/曝光率与组间检验暂停，订单贡献额仍可核对。`
                : hasPathMismatch
                ? "存在下游人数超过曝光人数的分组，路径率不能按严格漏斗解释；请核查跨渠道触达与事件埋点。"
                : "访问、领取、行动和完整路径购买按同一匿名用户的事件时间顺序统计；“未形成”指活动期内已完成关键行动、但未走完本期完整购买路径的用户，不等于永久流失。"}
            </p>
          </div>
        </>
      ) : (
        <p className="mt-3 text-sm text-muted-foreground">当前范围没有可展示的分组数据。</p>
      )}
    </section>
  );
}
