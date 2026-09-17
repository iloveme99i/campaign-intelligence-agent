import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  ArrowRight,
  Check,
  ChevronDown,
  Database,
  FileUp,
  Layers3,
  Loader2,
  ShieldCheck,
} from "lucide-react";
import { listEngines } from "@/api/conversations";
import {
  createExampleSnapshot,
  getSnapshotCatalog,
  type CampaignSummary,
  type SnapshotCatalog,
} from "@/api/merchant";
import { useConversationsStore } from "@/store/conversations";
import type { ReviewScope, ReviewTask } from "@/types/merchant";
import { priorEqualPeriod } from "@/lib/dateRange";

const FILES = [
  ["campaigns", "活动配置", "目标、周期、主指标与归因窗口", true],
  ["events", "行为事件", "匿名用户的触达、进入、权益领取与关键行动", true],
  ["orders", "交易结果", "订单金额、优惠、退款与成本", true],
  ["costs", "活动成本", "按日期、渠道和实验组记录投入", true],
  [
    "incrementality",
    "同期对照面板",
    "可选 · 稳定经营单元的多期结果，用于 DiD 增量识别",
    false,
  ],
] as const;
type FileKey = (typeof FILES)[number][0];
const REQUIRED_FILE_KEYS = FILES.filter(([, , , required]) => required).map(
  ([key]) => key,
);
type ImportResult = {
  engine_name: string;
  row_counts: Record<FileKey, number>;
};

const metricNames: Record<CampaignSummary["primary_metric"], string> = {
  conversion_rate: "转化率",
  completed_orders: "完成订单",
  net_revenue: "净收入",
  roi: "投入产出比",
};

const DECISION_OPTIONS = [
  ["continue", "是否继续", "判断当前机制是否值得保留"],
  ["adjust", "如何调整", "定位下一轮需要改变的对象"],
  ["scale", "能否扩大", "判断扩大覆盖的条件与风险"],
  ["stop", "是否停止", "判断投入是否应当及时止损"],
] as const;

const RISK_OPTIONS = [
  ["goal", "目标达成", "主指标是否真正达到预设目标"],
  ["path", "路径损耗", "变化发生在哪个转化节点"],
  ["variant", "方案差异", "实验组差异是否足够可信"],
  ["cost", "成本价值", "新增转化是否覆盖额外投入"],
] as const;

function money(cents: number) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  }).format(cents / 100);
}

function targetValue(campaign: CampaignSummary) {
  if (campaign.primary_metric === "conversion_rate")
    return new Intl.NumberFormat("zh-CN", {
      style: "percent",
      maximumFractionDigits: 1,
    }).format(Number(campaign.target_value));
  if (campaign.primary_metric === "net_revenue")
    return money(Number(campaign.target_value));
  if (campaign.primary_metric === "roi")
    return `${Number(campaign.target_value).toFixed(2)}×`;
  return Number(campaign.target_value).toLocaleString("zh-CN");
}

function initialScope(campaign: CampaignSummary): ReviewScope {
  return {
    campaign_id: campaign.campaign_id,
    baseline: priorEqualPeriod(campaign.start_date, campaign.end_date),
    activity: { start: campaign.start_date, end: campaign.end_date },
    variant: null,
    channel: null,
    location_id: null,
    audience: null,
    refund_basis: "after_refunds",
  };
}

export function MerchantWelcome({
  onSend,
}: {
  onSend: (
    text: string,
    engineName: string,
    scope: ReviewScope,
    task: ReviewTask,
  ) => Promise<void>;
}) {
  const { engines, setEngines } = useConversationsStore();
  const snapshots = engines.filter(
    (engine) => engine.type === "merchant_snapshot",
  );
  const snapshotKey = snapshots.map((snapshot) => snapshot.name).join("|");
  const [selectedEngine, setSelectedEngine] = useState("");
  const [snapshotLabels, setSnapshotLabels] = useState<Record<string, string>>({});
  const activeEngine = selectedEngine || snapshots[0]?.name || "";
  const [catalog, setCatalog] = useState<SnapshotCatalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogReload, setCatalogReload] = useState(0);
  const [campaignId, setCampaignId] = useState("");
  const [decisionIntent, setDecisionIntent] = useState("");
  const [riskFocus, setRiskFocus] = useState("cost");
  const [businessContext, setBusinessContext] = useState("");
  const [files, setFiles] = useState<Partial<Record<FileKey, File>>>({});
  const [importOpen, setImportOpen] = useState(false);
  const [working, setWorking] = useState<"import" | "demo" | "start" | null>(
    null,
  );
  const [error, setError] = useState("");
  const [imported, setImported] = useState<ImportResult | null>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    const names = snapshotKey ? snapshotKey.split("|") : [];
    if (!names.length) {
      setSnapshotLabels({});
      return;
    }
    const abort = new AbortController();
    void Promise.allSettled(
      names.map((name) => getSnapshotCatalog(name, abort.signal)),
    ).then((results) => {
      if (abort.signal.aborted) return;
      setSnapshotLabels(
        Object.fromEntries(
          results.flatMap((result, index) => {
            if (result.status !== "fulfilled") return [];
            const { campaigns } = result.value;
            const firstName = campaigns[0]?.campaign_name;
            if (!firstName) return [];
            return [[
              names[index],
              campaigns.length > 1
                ? `${firstName} 等 ${campaigns.length} 个活动`
                : firstName,
            ]];
          }),
        ),
      );
    });
    return () => abort.abort();
  }, [snapshotKey]);

  useEffect(() => {
    if (!activeEngine) {
      setCatalog(null);
      setCatalogLoading(false);
      return;
    }
    const abort = new AbortController();
    setCatalog(null);
    setCatalogLoading(true);
    setError("");
    getSnapshotCatalog(activeEngine, abort.signal)
      .then((data) => {
        setCatalog(data);
        setCampaignId((current) =>
          data.campaigns.some((item) => item.campaign_id === current)
            ? current
            : (data.campaigns[0]?.campaign_id ?? ""),
        );
        setCatalogLoading(false);
      })
      .catch((cause) => {
        if (!abort.signal.aborted) {
          setError(
            cause instanceof Error ? cause.message : "无法读取活动数据。",
          );
          setCatalogLoading(false);
        }
      });
    return () => abort.abort();
  }, [activeEngine, catalogReload]);
  useEffect(() => () => controller.current?.abort(), []);

  const campaign = useMemo(
    () =>
      catalog?.campaigns.find((item) => item.campaign_id === campaignId) ??
      catalog?.campaigns[0],
    [catalog, campaignId],
  );

  async function refreshWith(engineName: string) {
    setEngines(await listEngines());
    setSelectedEngine(engineName);
  }

  async function loadDemo() {
    if (working) return;
    setWorking("demo");
    setError("");
    try {
      const result = await createExampleSnapshot();
      await refreshWith(result.engine_name);
      setImported(result as ImportResult);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "合成示例创建失败。");
    } finally {
      setWorking(null);
    }
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (working || REQUIRED_FILE_KEYS.some((key) => !files[key])) return;
    if (
      Object.values(files).reduce((sum, file) => sum + file.size, 0) >
      20_000_000
    ) {
      setError("导入文件合计不能超过 20 MB。请缩小导出范围。");
      return;
    }
    setWorking("import");
    setError("");
    controller.current = new AbortController();
    try {
      const body = new FormData();
      FILES.forEach(([key]) => {
        const file = files[key];
        if (file) body.append(key, file);
      });
      const response = await fetch("/api/merchant/snapshots", {
        method: "POST",
        body,
        signal: controller.current.signal,
      });
      const data = await response.json();
      if (!response.ok)
        throw new Error(
          typeof data.detail === "string"
            ? data.detail
            : "导入失败，请核对文件格式。",
        );
      const result = data as ImportResult;
      await refreshWith(result.engine_name);
      setImported(result);
      setImportOpen(false);
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError"))
        setError(cause instanceof Error ? cause.message : "连接失败，请重试。");
    } finally {
      setWorking(null);
    }
  }

  async function start() {
    if (!campaign || !activeEngine || working || !decisionIntent) return;
    setWorking("start");
    setError("");
    const decision = DECISION_OPTIONS.find(([key]) => key === decisionIntent)?.[1];
    const risk = RISK_OPTIONS.find(([key]) => key === riskFocus)?.[1];
    const context = businessContext.trim();
    const request = [
      `复盘「${campaign.campaign_name}」。本轮需要决定：${decision}；优先判断：${risk}。`,
      "请核对目标结果、用户路径、方案差异与活动成本，区分已确认事实、解释假设和待验证项，并给出包含验证指标、护栏指标及停止/继续条件的下一轮行动。",
      context ? `已知业务变化或限制：${context}` : "未补充其他业务变化或限制。",
    ].join("\n");
    try {
      await onSend(request, activeEngine, initialScope(campaign), {
        decision_intent: decisionIntent as ReviewTask["decision_intent"],
        risk_focus: riskFocus as ReviewTask["risk_focus"],
        business_context: context,
      });
    } catch {
      setError("复盘会话创建失败，输入已保留，请重试。");
      setWorking(null);
    }
  }

  return (
    <main className="campaign-home flex-1 overflow-y-auto bg-background text-foreground">
      <div className="mx-auto min-h-full max-w-[1180px] px-5 py-8 md:px-10 md:py-10">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-[28px] font-semibold leading-tight tracking-[-0.025em]">
              新建决策记录
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              先说明这次复盘要支持什么决定，其余口径由系统从数据中核对。
            </p>
          </div>
          <div className="grid min-w-[280px] gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
            <label className="min-w-0 text-xs font-medium text-muted-foreground">
              数据快照
              <select
                value={activeEngine}
                onChange={(event) => setSelectedEngine(event.target.value)}
                disabled={Boolean(working)}
                className="mt-1.5 block h-11 w-full rounded-lg border border-border bg-background px-3 text-base text-foreground outline-none transition-[border-color,box-shadow] focus-visible:ring-2 focus-visible:ring-primary sm:text-sm"
              >
                {!snapshots.length && <option value="">尚未导入</option>}
                {snapshots.map((snapshot, index) => (
                  <option key={snapshot.name} value={snapshot.name}>
                    {snapshotLabels[snapshot.name] ?? "活动数据"} · 数据集 {index + 1}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={loadDemo}
              disabled={Boolean(working)}
              aria-busy={working === "demo"}
              className="inline-flex h-11 items-center justify-center gap-2 whitespace-nowrap rounded-lg border border-border bg-background px-3 text-sm font-medium text-foreground outline-none transition-[background-color,border-color,transform] hover:border-primary/35 hover:bg-secondary focus-visible:ring-2 focus-visible:ring-primary active:translate-y-px disabled:cursor-not-allowed disabled:opacity-55"
            >
              {working === "demo" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Layers3 className="h-4 w-4" aria-hidden="true" />
              )}
              {working === "demo" ? "正在载入" : "载入案例"}
            </button>
          </div>
        </header>

        {catalog && (
          <div className="mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 border-y border-border py-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-2 font-medium text-foreground">
              <ShieldCheck
                className="h-4 w-4 text-primary"
                aria-hidden="true"
              />
              快照校验通过
            </span>
            <span>{catalog.campaign_count.toLocaleString("zh-CN")} 个活动</span>
            <span>
              {catalog.event_count.toLocaleString("zh-CN")} 条行为事件
            </span>
            <span>
              {catalog.completed_order_count.toLocaleString("zh-CN")} 笔完成订单
            </span>
            <span>事件按匿名用户去重 · 订单按完成状态</span>
          </div>
        )}

        {catalog && campaign ? (
          <section className="grid min-h-[520px] overflow-hidden rounded-2xl border border-border bg-background lg:grid-cols-[300px_minmax(0,1fr)]">
            <aside className="hidden border-b border-border bg-muted/35 p-3 lg:block lg:border-b-0 lg:border-r">
              <div className="flex items-center justify-between px-2 py-2">
                <h2 className="text-sm font-semibold">活动</h2>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {catalog.campaign_count}
                </span>
              </div>
              <div className="mt-2 space-y-1">
                {catalog.campaigns.map((item) => {
                  const selected = item.campaign_id === campaign.campaign_id;
                  return (
                    <button
                      key={item.campaign_id}
                      type="button"
                      onClick={() => setCampaignId(item.campaign_id)}
                      className={`w-full rounded-xl px-3 py-3 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary ${selected ? "bg-background shadow-sm ring-1 ring-border" : "hover:bg-background/70"}`}
                    >
                      <span className="block truncate text-sm font-medium">
                        {item.campaign_name}
                      </span>
                      <span className="mt-1 block text-xs tabular-nums text-muted-foreground">
                        {item.start_date} — {item.end_date}
                      </span>
                    </button>
                  );
                })}
              </div>
            </aside>

            <div key={campaign.campaign_id} className="campaign-detail-enter flex min-w-0 flex-col p-5 md:p-8">
              <label className="order-1 mb-5 text-xs font-medium text-muted-foreground lg:hidden">
                选择活动
                <select
                  value={campaign.campaign_id}
                  onChange={(event) => setCampaignId(event.target.value)}
                  className="mt-1.5 block h-11 w-full rounded-lg border border-border bg-background px-3 text-base font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary sm:text-sm"
                >
                  {catalog.campaigns.map((item) => (
                    <option key={item.campaign_id} value={item.campaign_id}>
                      {item.campaign_name}
                    </option>
                  ))}
                </select>
                <span className="mt-2 block tabular-nums text-xs font-normal text-muted-foreground">
                  {campaign.start_date} — {campaign.end_date}
                </span>
              </label>
              <div className="order-1 flex flex-wrap items-start justify-between gap-5 border-b border-border pb-6">
                <div className="min-w-0 max-w-2xl">
                  <h2 className="break-words text-2xl font-semibold tracking-[-0.02em]">
                    {campaign.campaign_name}
                  </h2>
                  <p className="mt-2 max-w-[68ch] text-sm leading-6 text-muted-foreground">
                    {campaign.objective}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {catalog.synthetic && (
                    <span className="rounded-md border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-muted-foreground">
                      合成数据
                    </span>
                  )}
                  <span className="rounded-md bg-secondary px-2.5 py-1.5 text-xs font-medium text-secondary-foreground">
                    待复盘
                  </span>
                </div>
              </div>

              <dl className="order-3 grid border-b border-border sm:grid-cols-2 xl:grid-cols-4">
                {[
                  ["主指标", metricNames[campaign.primary_metric]],
                  ["目标值", targetValue(campaign)],
                  ["触达用户", campaign.exposed_users.toLocaleString("zh-CN")],
                  ["活动成本", money(campaign.activity_cost_cents)],
                ].map(([label, value], index) => (
                  <div
                    key={label}
                    className={`py-5 sm:px-5 ${index % 2 === 0 ? "sm:pl-0" : ""} xl:border-l xl:border-border xl:first:border-l-0`}
                  >
                    <dt className="text-xs text-muted-foreground">{label}</dt>
                    <dd className="mt-2 text-lg font-semibold tabular-nums">
                      {value}
                    </dd>
                  </div>
                ))}
              </dl>

              <div className="order-2 border-b border-border py-6">
                <div className="flex items-baseline justify-between gap-4">
                  <div>
                    <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
                      复盘任务单
                    </p>
                    <h3 className="mt-1.5 text-base font-semibold">
                      复盘结束后，你需要做什么决定？
                    </h3>
                  </div>
                  <span className="text-xs text-red-700">必选</span>
                </div>
                <div className="mt-4 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
                  {DECISION_OPTIONS.map(([key, label, description]) => (
                    <label
                      key={key}
                      className={`cursor-pointer bg-background p-3.5 outline-none transition-colors hover:bg-muted/60 ${decisionIntent === key ? "bg-secondary ring-1 ring-inset ring-primary" : ""}`}
                    >
                      <span className="flex items-center gap-2 text-sm font-semibold">
                        <input
                          type="radio"
                          name="decision-intent"
                          value={key}
                          checked={decisionIntent === key}
                          onChange={() => setDecisionIntent(key)}
                          className="h-4 w-4 accent-[hsl(var(--primary))]"
                        />
                        {label}
                      </span>
                      <span className="mt-1.5 block pl-6 text-xs leading-5 text-muted-foreground">
                        {description}
                      </span>
                    </label>
                  ))}
                </div>

                <h3 className="mt-6 text-sm font-semibold">
                  本轮优先判断哪类风险？
                </h3>
                <div className="mt-3 flex flex-wrap gap-2">
                  {RISK_OPTIONS.map(([key, label, description]) => (
                    <button
                      key={key}
                      type="button"
                      title={description}
                      aria-pressed={riskFocus === key}
                      onClick={() => setRiskFocus(key)}
                      className={`rounded-md border px-3 py-2 text-sm outline-none transition-[background-color,border-color,color,transform] focus-visible:ring-2 focus-visible:ring-primary active:translate-y-px ${riskFocus === key ? "border-primary bg-secondary font-medium text-secondary-foreground" : "border-border bg-background text-muted-foreground hover:border-primary/30 hover:text-foreground"}`}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                <label htmlFor="business-context" className="mt-6 block text-sm font-semibold">
                  已知业务变化或限制
                  <span className="ml-2 font-normal text-muted-foreground">可选</span>
                </label>
                <textarea
                  id="business-context"
                  rows={2}
                  maxLength={1000}
                  value={businessContext}
                  onChange={(event) => setBusinessContext(event.target.value)}
                  placeholder="例如：活动期部分门店缺货，或同期调整过价格。没有可留空。"
                  className="mt-2 block w-full resize-y rounded-lg border border-border bg-background p-3.5 text-base leading-6 outline-none transition-[border-color,box-shadow] placeholder:text-muted-foreground/70 focus-visible:ring-2 focus-visible:ring-primary"
                />
              </div>
              <div className="order-4 mt-5 flex flex-wrap items-center justify-between gap-3">
                <div className="text-xs leading-5 text-muted-foreground">
                  <p>系统自动核对：目标 · 路径 · 方案 · 成本</p>
                  <p>归因窗口 {campaign.attribution_days} 天 · 订单按完成状态统计</p>
                </div>
                <button
                  type="button"
                  onClick={start}
                  disabled={Boolean(working) || !decisionIntent}
                  aria-busy={working === "start"}
                  className="inline-flex h-11 items-center gap-2 whitespace-nowrap rounded-lg bg-primary px-5 text-sm font-semibold text-primary-foreground shadow-[0_8px_20px_-14px_hsl(var(--foreground)/0.75)] outline-none transition-[background-color,transform,box-shadow] hover:bg-primary/90 hover:shadow-[0_10px_24px_-14px_hsl(var(--foreground)/0.75)] focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-55 disabled:shadow-none"
                >
                  {working === "start" ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <ArrowRight className="h-4 w-4" />
                  )}
                  {working === "start" ? "正在建立任务…" : "确认任务并开始核算"}
                </button>
              </div>
            </div>
          </section>
        ) : catalogLoading ? (
          <section
            className="flex min-h-[420px] flex-col items-center justify-center border-y border-border px-6 py-16 text-center"
            role="status"
            aria-live="polite"
          >
            <Loader2 className="h-7 w-7 animate-spin text-primary" aria-hidden="true" />
            <h2 className="mt-5 text-lg font-semibold">正在校验数据快照</h2>
            <p className="mt-2 text-sm text-muted-foreground">核对活动范围、事件口径和交易结果…</p>
          </section>
        ) : activeEngine && error ? (
          <section className="flex min-h-[420px] flex-col items-center justify-center border-y border-border px-6 py-16 text-center">
            <Database className="h-8 w-8 text-red-700" strokeWidth={1.5} aria-hidden="true" />
            <h2 className="mt-5 text-lg font-semibold">数据快照未能打开</h2>
            <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">{error}</p>
            <button
              type="button"
              onClick={() => setCatalogReload((value) => value + 1)}
              className="mt-6 inline-flex h-11 items-center rounded-lg border border-border bg-background px-4 text-sm font-semibold outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary active:translate-y-px"
            >
              重新读取
            </button>
          </section>
        ) : (
          <section className="flex min-h-[420px] flex-col items-center justify-center border-y border-border px-6 py-16 text-center">
            <Database
              className="h-8 w-8 text-muted-foreground"
              strokeWidth={1.5}
            />
            <h2 className="mt-5 text-lg font-semibold">导入一份活动数据</h2>
            <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
              需要活动配置、匿名行为事件、交易结果和活动成本。也可以先用跨业务合成案例检查完整分析链。
            </p>
            <button
              type="button"
              onClick={loadDemo}
              disabled={Boolean(working)}
              aria-busy={working === "demo"}
              className="mt-6 inline-flex h-11 items-center gap-2 whitespace-nowrap rounded-lg bg-primary px-5 text-sm font-semibold text-primary-foreground outline-none transition-[background-color,transform] hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-55"
            >
              {working === "demo" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ArrowRight className="h-4 w-4" />
              )}
              {working === "demo" ? "正在创建…" : "打开跨业务合成案例"}
            </button>
          </section>
        )}

        {imported && (
          <p
            role="status"
            className="mt-5 flex items-center gap-2 text-sm text-emerald-700"
          >
            <Check className="h-4 w-4" />
            已导入 {imported.row_counts.campaigns} 个活动、
            {imported.row_counts.events.toLocaleString("zh-CN")} 条行为事件。
            {imported.row_counts.incrementality > 0 &&
              ` 已纳入 ${imported.row_counts.incrementality.toLocaleString("zh-CN")} 行同期对照面板。`}
          </p>
        )}
        <section className="mt-6 border-y border-border py-5">
          <button
            type="button"
            aria-expanded={importOpen}
            onClick={() => setImportOpen((open) => !open)}
            className="group flex w-full items-center justify-between gap-4 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <span className="flex items-center gap-3">
              <FileUp className="h-4 w-4 text-muted-foreground" />
              <span>
                <span className="block text-sm font-medium">导入活动数据</span>
                <span className="mt-1 block text-xs text-muted-foreground">
                  CSV · 本地校验 · 不包含姓名或手机号
                </span>
              </span>
            </span>
            <ChevronDown
              className={`h-4 w-4 transition-transform duration-200 ${importOpen ? "rotate-180" : ""}`}
            />
          </button>
          {importOpen && (
            <form onSubmit={upload} className="import-panel mt-6 space-y-5">
              <div className="grid gap-x-8 gap-y-5 md:grid-cols-2">
                {FILES.map(([key, label, hint, required]) => (
                  <label key={key} className="text-sm">
                    <span className="font-medium">{label}</span>
                    {!required && (
                      <span className="ml-2 rounded-full border border-border px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">
                        因果识别可选
                      </span>
                    )}
                    <span className="ml-2 text-xs text-muted-foreground">
                      {hint}
                    </span>
                    <input
                      type="file"
                      accept=".csv,text/csv"
                      required={required}
                      disabled={Boolean(working)}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        setFiles((old) => ({ ...old, [key]: file }));
                        setError("");
                      }}
                      className="mt-2 block w-full text-sm file:mr-3 file:h-10 file:rounded-lg file:border file:border-border file:bg-background file:px-3 file:text-sm file:font-medium hover:file:bg-muted"
                    />
                  </label>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-4">
                <button
                  type="submit"
                  disabled={
                    Boolean(working) || FILES.some(([key]) => !files[key])
                  }
                  className="inline-flex h-11 items-center gap-2 whitespace-nowrap rounded-lg bg-foreground px-4 text-sm font-medium text-background disabled:cursor-not-allowed disabled:opacity-55"
                >
                  {working === "import" && (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  )}
                  {working === "import" ? "正在校验…" : "校验并导入"}
                </button>
                <a
                  href="/api/merchant/examples.zip?templates_only=true"
                  className="whitespace-nowrap text-sm text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground"
                >
                  下载空白模板
                </a>
              </div>
            </form>
          )}
        </section>
        {error && (catalog || !activeEngine) && (
          <p
            role="alert"
            className="mt-5 rounded-lg border border-red-500/30 bg-red-500/5 p-4 text-sm text-red-700"
          >
            {error}
          </p>
        )}
      </div>
    </main>
  );
}
