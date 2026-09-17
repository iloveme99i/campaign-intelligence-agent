import { useEffect, useState, type FormEvent } from "react";
import { ChevronDown } from "lucide-react";
import type { ReviewScope } from "@/types/merchant";
import {
  getSnapshotCatalog,
  type CampaignSummary,
  type SnapshotCatalog,
} from "@/api/merchant";
import { priorEqualPeriod } from "@/lib/dateRange";

interface Props {
  engineName: string;
  confirmed?: ReviewScope;
  disabled: boolean;
  onConfirm: (scope: ReviewScope) => void;
}

const emptyScope: ReviewScope = {
  campaign_id: "",
  baseline: { start: "", end: "" },
  activity: { start: "", end: "" },
  variant: null,
  channel: null,
  location_id: null,
  audience: null,
  refund_basis: "after_refunds",
};

function scopeForCampaign(
  campaign: CampaignSummary,
  current: ReviewScope,
): ReviewScope {
  return {
    ...current,
    campaign_id: campaign.campaign_id,
    activity: { start: campaign.start_date, end: campaign.end_date },
    baseline: priorEqualPeriod(campaign.start_date, campaign.end_date),
  };
}

export function ScopeEditor({
  engineName,
  confirmed,
  disabled,
  onConfirm,
}: Props) {
  const [draft, setDraft] = useState<ReviewScope>(confirmed ?? emptyScope);
  const [catalog, setCatalog] = useState<SnapshotCatalog | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    getSnapshotCatalog(engineName, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setCatalog(data);
        if (!confirmed && data.campaigns[0])
          setDraft((current) => scopeForCampaign(data.campaigns[0], current));
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError("活动范围读取失败，请重新导入数据。");
      });
    return () => controller.abort();
  }, [engineName, confirmed]);

  function confirm(event: FormEvent) {
    event.preventDefault();
    if (disabled) return;
    if (
      !draft.campaign_id ||
      !draft.baseline.start ||
      !draft.baseline.end ||
      !draft.activity.start ||
      !draft.activity.end
    ) {
      setError("请选择活动并填写完整日期。");
      return;
    }
    if (
      draft.baseline.start > draft.baseline.end ||
      draft.activity.start > draft.activity.end
    ) {
      setError("结束日期不能早于开始日期。");
      return;
    }
    if (
      draft.baseline.start <= draft.activity.end &&
      draft.activity.start <= draft.baseline.end
    ) {
      setError("对比期与活动期不能重叠。");
      return;
    }
    setError("");
    onConfirm(draft);
  }

  const campaignName = catalog?.campaigns.find(
    (item) => item.campaign_id === draft.campaign_id,
  )?.campaign_name;
  return (
    <details
      className="scope-strip group border-b border-border bg-muted/25"
      data-print-hide
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary md:px-7">
        <span className="min-w-0 flex-1 sm:flex sm:items-baseline sm:gap-3">
          <strong className="block truncate font-medium">
            {confirmed
              ? (campaignName ?? confirmed.campaign_id)
              : "确认复盘口径"}
          </strong>
          <span className="mt-0.5 block shrink-0 whitespace-nowrap text-xs text-muted-foreground sm:mt-0 sm:text-sm">
            {confirmed
              ? `${confirmed.activity.start} — ${confirmed.activity.end}`
              : "活动、周期与归因范围"}
          </span>
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-180" />
      </summary>
      <form
        onSubmit={confirm}
        className="border-t border-border px-5 py-5 md:px-7"
      >
        <fieldset disabled={disabled} className="space-y-5 disabled:opacity-60">
          <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr_1fr]">
            <label className="text-xs font-medium text-muted-foreground">
              活动
              <select
                required
                value={draft.campaign_id}
                onChange={(event) => {
                  const selected = catalog?.campaigns.find(
                    (item) => item.campaign_id === event.target.value,
                  );
                  setDraft((current) =>
                    selected
                      ? scopeForCampaign(selected, current)
                      : { ...current, campaign_id: event.target.value },
                  );
                }}
                className="mt-1.5 block h-11 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
              >
                <option value="">选择活动</option>
                {catalog?.campaigns.map((item) => (
                  <option key={item.campaign_id} value={item.campaign_id}>
                    {item.campaign_name}
                  </option>
                ))}
              </select>
            </label>
            {(["baseline", "activity"] as const).map((period) => (
              <fieldset key={period} className="grid grid-cols-2 gap-2">
                <legend className="mb-1.5 text-xs font-medium text-muted-foreground">
                  {period === "baseline" ? "对比期" : "活动期"}
                </legend>
                {(["start", "end"] as const).map((edge) => (
                  <input
                    key={edge}
                    type="date"
                    required
                    aria-label={`${period === "baseline" ? "对比期" : "活动期"}${edge === "start" ? "开始" : "结束"}`}
                    value={draft[period][edge]}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        [period]: {
                          ...current[period],
                          [edge]: event.target.value,
                        },
                      }))
                    }
                    className="h-11 min-w-0 rounded-lg border border-border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  />
                ))}
              </fieldset>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              ["variant", "实验组", catalog?.variants ?? []],
              ["channel", "渠道", catalog?.channels ?? []],
              ["location_id", "经营点", catalog?.locations ?? []],
              ["audience", "目标人群", catalog?.audiences ?? []],
            ].map(([key, label, choices]) => (
              <label
                key={key as string}
                className="text-xs font-medium text-muted-foreground"
              >
                {label as string}
                <select
                  value={
                    (draft[key as keyof ReviewScope] as string | null) ?? ""
                  }
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      [key as string]: event.target.value || null,
                    }))
                  }
                  className="mt-1.5 block h-11 w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                >
                  <option value="">全部</option>
                  {(choices as string[]).map((choice) => (
                    <option key={choice}>{choice}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <p className="text-xs leading-5 text-muted-foreground">
              事件节点按去重用户统计；修改口径会产生一组新证据，旧结论不会被覆盖。
            </p>
            <button
              type="submit"
              className="h-10 whitespace-nowrap rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground outline-none hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
            >
              确认并计算
            </button>
          </div>
        </fieldset>
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {error}
          </p>
        )}
      </form>
    </details>
  );
}
