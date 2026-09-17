import { ArrowRight, ChevronDown, History } from "lucide-react";
import type { ScopeRevision } from "@/api/merchant";

const integer = new Intl.NumberFormat("zh-CN");
const money = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

function formatValue(value: number, unit: "count" | "cents") {
  return unit === "cents" ? money.format(value / 100) : integer.format(value);
}

function formatDelta(value: number, unit: "count" | "cents") {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${formatValue(value, unit)}`;
}

export function ScopeRevisionNotice({
  revision,
}: {
  revision?: ScopeRevision;
}) {
  if (!revision || revision.status !== "revised") return null;

  return (
    <details className="group mb-4 border-y border-amber-300/70 bg-amber-50/45 px-3 py-3">
      <summary className="flex cursor-pointer list-none items-start justify-between gap-3 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-primary">
        <span className="flex min-w-0 items-start gap-2.5">
          <History className="mt-0.5 h-4 w-4 shrink-0 text-amber-800" />
          <span>
            <span className="block text-xs font-semibold text-amber-950">
              口径已修订 · 旧结论已失效
            </span>
            <span className="mt-1 block text-[11px] leading-5 text-amber-900/75">
              已按新范围重新计算，展开核对前后变化
            </span>
          </span>
        </span>
        <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-amber-800 transition-transform duration-200 group-open:rotate-180" />
      </summary>

      <div className="mt-4 border-t border-amber-300/70 pt-4">
        <p className="text-xs leading-5 text-amber-950">
          {revision.reason}
          {revision.previous_decision_superseded
            ? " 上一口径已落档的决策也需重新确认。"
            : ""}
        </p>

        <div className="mt-4 space-y-3">
          {revision.changed_fields.map((item) => (
            <div key={item.key} className="grid grid-cols-[5rem_minmax(0,1fr)] gap-3 text-xs">
              <span className="text-amber-900/70">{item.label}</span>
              <span className="flex min-w-0 items-center gap-2 font-medium text-amber-950">
                <span className="truncate line-through decoration-amber-700/50">
                  {item.previous}
                </span>
                <ArrowRight className="h-3.5 w-3.5 shrink-0 text-amber-700" />
                <span className="truncate">{item.current}</span>
              </span>
            </div>
          ))}
        </div>

        {revision.metric_changes.length > 0 && (
          <div className="mt-4 overflow-hidden border-t border-amber-300/70 pt-3">
            <p className="mb-2 text-[11px] font-semibold tracking-wide text-amber-900/70">
              活动期指标重算
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {revision.metric_changes.map((item) => (
                <div key={item.key} className="flex items-baseline justify-between gap-3 text-xs">
                  <span className="text-amber-900/70">{item.label}</span>
                  <span className="text-right font-medium tabular-nums text-amber-950">
                    {formatValue(item.current, item.unit)}
                    <span
                      className={`ml-1.5 text-[11px] ${item.delta < 0 ? "text-red-700" : item.delta > 0 ? "text-emerald-700" : "text-amber-900/60"}`}
                    >
                      {formatDelta(item.delta, item.unit)}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </details>
  );
}
