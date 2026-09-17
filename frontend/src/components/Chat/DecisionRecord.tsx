import { CheckCircle2, Copy, ShieldAlert } from "lucide-react";
import { useState } from "react";
import type { UIMessage } from "@/types";
import type { ReviewTask } from "@/types/merchant";
import { BUSINESS_TERM_NAMES } from "@/lib/merchantNames";
import {
  commitDecision,
  type DecisionCommit,
  type DecisionOutcomeRecord,
  type ExperimentPlan as ExperimentPlanData,
} from "@/api/merchant";
import { TextMessage } from "./messages/TextMessage";
import { ExperimentPlan } from "./ExperimentPlan";
import { OutcomeReview } from "./OutcomeReview";

type SectionKey =
  | "conclusion"
  | "costBoundary"
  | "supersession"
  | "facts"
  | "hypotheses"
  | "unknowns"
  | "action";

const SECTION_PATTERNS: Array<[SectionKey, RegExp]> = [
  [
    "conclusion",
    /^(?:#{1,6}\s*)(?:(?:[一二三四五六七八九十]+、)|(?:\d+[.、]))?\s*(?:总盘)?结论(?:一句话)?[^\n]*$/m,
  ],
  [
    "costBoundary",
    /^(?:#{1,6}\s*)(?:(?:[一二三四五六七八九十]+、)|(?:\d+[.、]))?\s*成本归属核对[^\n]*$/m,
  ],
  [
    "supersession",
    /^(?:#{1,6}\s*)(?:(?:[一二三四五六七八九十]+、)|(?:\d+[.、]))?\s*旧结论失效说明[^\n]*$/m,
  ],
  [
    "facts",
    /^(?:#{1,6}\s*)?(?:\*\*)?(?:(?:[一二三四五六七八九十]+、)|(?:\d+[.、]))?\s*已确认事实[^\n]*$/m,
  ],
  [
    "hypotheses",
    /^(?:#{1,6}\s*)?(?:\*\*)?(?:(?:[一二三四五六七八九十]+、)|(?:\d+[.、]))?\s*解释假设[^\n]*$/m,
  ],
  [
    "unknowns",
    /^(?:#{1,6}\s*)?(?:\*\*)?(?:(?:[一二三四五六七八九十]+、)|(?:\d+[.、]))?\s*待验证项[^\n]*$/m,
  ],
  [
    "action",
    /^(?:#{1,6}\s*)?(?:\*\*)?(?:(?:[一二三四五六七八九十]+、)|(?:\d+[.、]))?\s*下一轮行动[^\n]*$/m,
  ],
];

const DECISION_NAMES: Record<ReviewTask["decision_intent"], string> = {
  continue: "是否继续",
  adjust: "如何调整",
  scale: "能否扩大",
  stop: "是否停止",
};

const RISK_NAMES: Record<ReviewTask["risk_focus"], string> = {
  goal: "目标达成",
  path: "路径损耗",
  variant: "方案差异",
  cost: "成本价值",
};

type DecisionOutcome = NonNullable<DecisionCommit["decision_outcome"]>;
type DecisionReason = NonNullable<DecisionCommit["reason_code"]>;

const OUTCOME_NAMES: Record<DecisionOutcome, string> = {
  adopted: "按建议执行",
  modified: "修改后执行",
  rejected: "暂不采用",
};

const REASON_NAMES: Record<DecisionReason, string> = {
  evidence_supported: "证据支持当前判断",
  execution_constraint: "执行条件需要调整",
  insufficient_evidence: "现有证据不足",
  risk_too_high: "风险或成本不可接受",
  priority_changed: "业务优先级已变化",
  other: "其他原因",
};

const EVIDENCE_KIND_NAMES: Record<string, string> = {
  outcome: "经营结果",
  funnel: "用户路径",
  cost: "活动成本",
  target: "目标配置",
  diagnostic: "定向诊断",
  "diagnosis:variant:cost": "共享费用明细",
  "diagnosis:channel:cost": "渠道费用明细",
};

export function parseDecisionSections(
  text: string,
): Partial<Record<SectionKey, string>> {
  const matches = SECTION_PATTERNS.flatMap(([key, pattern]) => {
    const match = pattern.exec(text);
    return match
      ? [{ key, index: match.index, end: match.index + match[0].length }]
      : [];
  }).sort((a, b) => a.index - b.index);

  const sections = matches.reduce<Partial<Record<SectionKey, string>>>(
    (result, item, index) => {
      let content = text
        .slice(item.end, matches[index + 1]?.index ?? text.length)
        .trim();
      if (item.key === "action") {
        content = content.replace(/\n+需要我[^\n]*[？?]\s*$/, "").trim();
      }
      result[item.key] = content;
      return result;
    },
    {},
  );
  if (!sections.conclusion && matches[0] && matches[0].key === "facts") {
    sections.conclusion = text
      .slice(0, matches[0].index)
      .trim()
      .replace(/^#{1,6}\s+[^\n]+(?:复盘|分析)[^\n]*\n+/i, "")
      .trim();
  }
  return sections;
}

export function evidenceCoverage(
  answer: string,
  evidence?: UIMessage,
  diagnosticEvidence: UIMessage[] = [],
) {
  const available = [evidence, ...diagnosticEvidence]
    .flatMap((message) => [
      message?.payload.evidence_id,
      ...(Array.isArray(message?.payload.related_evidence_ids)
        ? message.payload.related_evidence_ids
        : []),
      ...(Array.isArray(message?.payload.analysis_manifest)
        ? message.payload.analysis_manifest.map((item: { evidence_id?: string }) => item.evidence_id)
        : []),
    ])
    .filter(
      (id): id is string => typeof id === "string" && id.startsWith("q_"),
    );
  const uniqueAvailable = [...new Set(available)];
  const cited = new Set(answer.match(/q_[0-9a-f]{8,}/g) ?? []);
  return {
    cited: uniqueAvailable.filter((id) => cited.has(id)).length,
    total: uniqueAvailable.length,
  };
}

function evidenceLabels(
  evidence?: UIMessage,
  diagnosticEvidence: UIMessage[] = [],
) {
  const labels = new Map<string, string>();
  const manifest = evidence?.payload.evidence_manifest;
  if (Array.isArray(manifest)) {
    for (const item of manifest) {
      if (!item || typeof item !== "object") continue;
      const id = (item as { evidence_id?: unknown }).evidence_id;
      const kind = (item as { kind?: unknown }).kind;
      if (typeof id === "string" && typeof kind === "string") {
        labels.set(id, EVIDENCE_KIND_NAMES[kind] ?? "计算证据");
      }
    }
  }
  const analysisManifest = evidence?.payload.analysis_manifest;
  if (Array.isArray(analysisManifest)) {
    for (const item of analysisManifest) {
      if (!item || typeof item !== "object") continue;
      const entry = item as { evidence_id?: unknown; kind?: unknown };
      if (typeof entry.evidence_id !== "string" || typeof entry.kind !== "string") continue;
      const label = entry.kind === "timeline" ? "时间走势" : entry.kind === "data_quality" ? "数据可信度" : entry.kind.startsWith("segment:") ? `分层扫描 · ${entry.kind.slice(8)}` : "计算证据";
      labels.set(entry.evidence_id, label);
    }
  }
  for (const message of diagnosticEvidence) {
    const id = message.payload.evidence_id;
    if (typeof id === "string") labels.set(id, "定向诊断");
    const diagnosticManifest = message.payload.evidence_manifest;
    if (!Array.isArray(diagnosticManifest)) continue;
    for (const item of diagnosticManifest) {
      if (!item || typeof item !== "object") continue;
      const entry = item as { evidence_id?: unknown; kind?: unknown };
      if (typeof entry.evidence_id !== "string") continue;
      labels.set(
        entry.evidence_id,
        typeof entry.kind === "string"
          ? (EVIDENCE_KIND_NAMES[entry.kind] ?? "定向诊断明细")
          : "定向诊断明细",
      );
    }
  }
  let index = 1;
  return (id: string) => {
    const existing = labels.get(id);
    if (existing) return existing;
    const fallback = `计算证据 ${String(index).padStart(2, "0")}`;
    index += 1;
    labels.set(id, fallback);
    return fallback;
  };
}

export function displayEvidenceReferences(
  text: string,
  evidence?: UIMessage,
  diagnosticEvidence: UIMessage[] = [],
) {
  const labelFor = evidenceLabels(evidence, diagnosticEvidence);
  const withEvidenceLabels = text.replace(/q_[0-9a-f]{8,}/g, (id) =>
    labelFor(id),
  );
  const withBusinessTerms = Object.entries(BUSINESS_TERM_NAMES).reduce(
    (result, [internalName, businessName]) =>
      result.replace(new RegExp(`\\b${internalName}\\b`, "g"), businessName),
    withEvidenceLabels,
  );
  return withBusinessTerms.replace(
    /experiment\.status\s*=\s*观察性对比/g,
    "实验状态为观察性对比",
  );
}

function Section({
  index,
  title,
  content,
}: {
  index?: string;
  title: string;
  content?: string;
}) {
  if (!content) return null;
  return (
    <section className="decision-record-section border-t border-border py-5">
      <div className="mb-3 flex items-baseline gap-3">
        {index && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {index}
          </span>
        )}
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      <TextMessage
        payload={{ text: content }}
        role="assistant"
        presentation="document"
        showCopy={false}
      />
    </section>
  );
}

export function DecisionRecord({
  answer,
  evidence,
  diagnosticEvidence = [],
  conversationId,
  committedDecision,
  recordedOutcome,
  reviewTask,
  auditPassed = false,
  onCommitted,
}: {
  answer: string;
  evidence?: UIMessage;
  diagnosticEvidence?: UIMessage[];
  conversationId: string;
  committedDecision?: DecisionCommit;
  recordedOutcome?: DecisionOutcomeRecord;
  reviewTask?: ReviewTask;
  auditPassed?: boolean;
  onCommitted?: (decision: DecisionCommit) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [owner, setOwner] = useState("");
  const [reviewDate, setReviewDate] = useState("");
  const [note, setNote] = useState("");
  const [decisionOutcome, setDecisionOutcome] = useState<DecisionOutcome>("adopted");
  const [reasonCode, setReasonCode] = useState<DecisionReason>("evidence_supported");
  const [rationale, setRationale] = useState("");
  const [finalAction, setFinalAction] = useState("");
  const [decision, setDecision] = useState<DecisionCommit | undefined>(
    committedDecision,
  );
  const [confirmedExperimentPlan, setConfirmedExperimentPlan] = useState<
    ExperimentPlanData | undefined
  >(committedDecision?.experiment_plan ?? undefined);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const sections = parseDecisionSections(answer);
  const coverage = evidenceCoverage(answer, evidence, diagnosticEvidence);
  const displayAnswer = displayEvidenceReferences(
    answer,
    evidence,
    diagnosticEvidence,
  );
  const structured = Object.keys(sections).length >= 4;
  const renderEvidence = (value: string) =>
    displayEvidenceReferences(value, evidence, diagnosticEvidence);
  const expectedScopeId =
    typeof evidence?.payload.scope_id === "string"
      ? evidence.payload.scope_id
      : "";
  const currentMetric = evidence?.payload.metric_version === "v0.8";
  const savedOutcome = decision?.decision_outcome ?? "adopted";
  const savedDecisionTone =
    savedOutcome === "rejected"
      ? "border-amber-300 text-amber-900"
      : savedOutcome === "modified"
        ? "border-primary/35 text-primary"
        : "border-emerald-300 text-emerald-800";

  if (!structured) {
    return (
      <TextMessage
        payload={{ text: displayAnswer }}
        role="assistant"
        presentation="document"
      />
    );
  }

  return (
    <article className="decision-record">
      {!currentMetric && <p className="mb-4 border-l-[3px] border-amber-600 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-950">历史分析基于旧指标口径，仅供回溯；请重新核算后再用于实验规划或经营决策。</p>}
      {currentMetric && !auditPassed && <p className="mb-4 border-l-[3px] border-amber-600 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-950">本轮分析尚未通过证据与决策边界审计；可以查看分析过程，但不能直接采用并落档。</p>}
      <div className="border-b border-border pb-5">
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs font-medium tracking-[0.12em] text-muted-foreground">
            本轮决策记录
          </span>
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard.writeText(displayAnswer).then(() => {
                setCopied(true);
                window.setTimeout(() => setCopied(false), 1600);
              });
            }}
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary"
          >
            {copied ? (
              <CheckCircle2 className="h-3.5 w-3.5" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
            {copied ? "已复制" : "复制记录"}
          </button>
        </div>
        {reviewTask && (
          <dl className="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5 border-y border-border py-3 text-xs">
            <dt className="text-muted-foreground">要支持的决策</dt>
            <dd className="font-semibold">
              {DECISION_NAMES[reviewTask.decision_intent]}
            </dd>
            <dt className="text-muted-foreground">优先判断</dt>
            <dd className="font-semibold">
              {RISK_NAMES[reviewTask.risk_focus]}
            </dd>
            {reviewTask.business_context && (
              <>
                <dt className="text-muted-foreground">已知限制</dt>
                <dd className="leading-5">{reviewTask.business_context}</dd>
              </>
            )}
          </dl>
        )}
        <div className="mt-3">
          <TextMessage
            payload={{
              text: displayEvidenceReferences(
                sections.conclusion ?? "本轮判断已生成。",
                evidence,
                diagnosticEvidence,
              ),
            }}
            role="assistant"
            presentation="document"
            showCopy={false}
          />
        </div>
        <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-700" />
            已引用 {coverage.cited}/{coverage.total} 项计算证据
          </span>
          <span className="inline-flex items-center gap-1.5">
            <ShieldAlert className="h-3.5 w-3.5" />
            生成式判断，经营决策前请核对证据
          </span>
        </div>
      </div>

      <Section
        index="01"
        title="已确认事实"
        content={renderEvidence(sections.facts ?? "")}
      />
      {sections.costBoundary && (
        <Section title="成本归属边界" content={renderEvidence(sections.costBoundary)} />
      )}
      {sections.supersession && (
        <Section title="旧结论撤销" content={renderEvidence(sections.supersession)} />
      )}
      <Section
        index="02"
        title="解释假设"
        content={renderEvidence(sections.hypotheses ?? "")}
      />
      <Section
        index="03"
        title="待验证项"
        content={renderEvidence(sections.unknowns ?? "")}
      />
      <Section
        index="04"
        title="下一轮行动"
        content={renderEvidence(sections.action ?? "")}
      />

      {expectedScopeId && currentMetric ? (
        <ExperimentPlan
          conversationId={conversationId}
          expectedScopeId={expectedScopeId}
          expectedAnswer={answer}
          committedPlan={decision?.experiment_plan ?? undefined}
          decisionLocked={Boolean(decision)}
          onPlanConfirmed={setConfirmedExperimentPlan}
          segments={evidence?.payload.segments as Record<string, Array<{ dimension_value: string; total_groups?: number }>> | undefined}
        />
      ) : (
        <p className="border-t border-border py-5 text-sm text-red-700">
          当前轮次缺少现行口径证据，不能进行实验测算或决策落档。
        </p>
      )}

      <section className="border-t border-border py-5">
        <div className="mb-3 flex items-baseline gap-3">
          <span className="font-mono text-[11px] text-muted-foreground">
            06
          </span>
          <h3 className="text-sm font-semibold">决策落档</h3>
        </div>
        {decision ? (
          <div className={`border-y py-4 ${savedDecisionTone}`}>
            <p className="text-sm font-semibold">
              {OUTCOME_NAMES[savedOutcome]}
            </p>
            <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-xs text-muted-foreground">负责人</dt>
                <dd className="mt-1 font-medium">{decision.owner}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">复查日期</dt>
                <dd className="mt-1 font-medium tabular-nums">
                  {decision.review_date}
                </dd>
              </div>
              {decision.rationale && (
                <div className="sm:col-span-2">
                  <dt className="text-xs text-muted-foreground">判断依据</dt>
                  <dd className="mt-1 leading-6">{decision.rationale}</dd>
                </div>
              )}
              {decision.final_action && (
                <div className="sm:col-span-2">
                  <dt className="text-xs text-muted-foreground">最终动作</dt>
                  <dd className="mt-1 leading-6">{decision.final_action}</dd>
                </div>
              )}
              {decision.note && (
                <div className="sm:col-span-2">
                  <dt className="text-xs text-muted-foreground">执行备注</dt>
                  <dd className="mt-1 leading-6">{decision.note}</dd>
                </div>
              )}
            </dl>
            <p className="mt-3 text-[11px] text-muted-foreground">
              已绑定本轮证据口径、回答版本与落档时质量门，后续修改不会覆盖这条记录。
            </p>
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              setSaving(true);
              setSaveError("");
              void commitDecision(conversationId, {
                owner: owner.trim(),
                review_date: reviewDate,
                note: note.trim(),
                decision_outcome: decisionOutcome,
                reason_code: reasonCode,
                rationale: rationale.trim(),
                final_action: finalAction.trim(),
                expected_scope_id: expectedScopeId,
                expected_answer: answer,
                ...(confirmedExperimentPlan && decisionOutcome !== "rejected"
                  ? {
                      experiment_mde_pp: confirmedExperimentPlan.mde_pp,
                      experiment_traffic_share:
                        confirmedExperimentPlan.traffic_share,
                      experiment_dimension: confirmedExperimentPlan.population.dimension,
                      experiment_value: confirmedExperimentPlan.population.value,
                      experiment_secondary_dimension: confirmedExperimentPlan.population.secondary_dimension ?? null,
                      experiment_secondary_value: confirmedExperimentPlan.population.secondary_value ?? null,
                      experiment_metric: confirmedExperimentPlan.population.metric,
                    }
                  : {}),
              })
                .then((result) => {
                  setDecision(result);
                  onCommitted?.(result);
                  setConfirmedExperimentPlan(
                    result.experiment_plan ?? confirmedExperimentPlan,
                  );
                })
                .catch((cause) =>
                  setSaveError(
                    cause instanceof Error
                      ? cause.message
                      : "决策落档失败，请重试。",
                  ),
                )
                .finally(() => setSaving(false));
            }}
          >
            <p className="text-sm leading-6 text-muted-foreground">
              记录业务负责人是否采纳本轮建议；修改或不采用时，原因会作为后续 Agent 评测依据。
            </p>
            <fieldset>
              <legend className="text-xs font-medium text-muted-foreground">业务决定</legend>
              <div className="mt-2 grid gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-3">
                {(Object.keys(OUTCOME_NAMES) as DecisionOutcome[]).map((outcome) => (
                  <label
                    key={outcome}
                    className={`cursor-pointer bg-background px-3 py-2.5 text-sm font-medium outline-none transition-colors hover:bg-muted ${decisionOutcome === outcome ? "text-primary ring-1 ring-inset ring-primary" : "text-foreground"}`}
                  >
                    <input
                      type="radio"
                      name="decision-outcome"
                      value={outcome}
                      checked={decisionOutcome === outcome}
                      onChange={() => {
                        setDecisionOutcome(outcome);
                        setReasonCode(outcome === "adopted" ? "evidence_supported" : outcome === "rejected" ? "insufficient_evidence" : "execution_constraint");
                      }}
                      className="sr-only"
                    />
                    {OUTCOME_NAMES[outcome]}
                  </label>
                ))}
              </div>
            </fieldset>
            {!auditPassed && decisionOutcome !== "rejected" && (
              <p className="text-sm leading-6 text-amber-800" role="status">
                本轮未通过质量门，只能记录“暂不采用”；修正并重新核算后才能采纳。
              </p>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-xs font-medium text-muted-foreground">
                判断原因
                <select
                  value={reasonCode}
                  onChange={(event) => setReasonCode(event.target.value as DecisionReason)}
                  className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                >
                  {(Object.keys(REASON_NAMES) as DecisionReason[]).map((reason) => (
                    <option key={reason} value={reason}>{REASON_NAMES[reason]}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs font-medium text-muted-foreground">
                判断依据
                <input
                  required
                  maxLength={1000}
                  value={rationale}
                  onChange={(event) => setRationale(event.target.value)}
                  placeholder="写明采用、调整或驳回的关键依据"
                  className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                />
              </label>
            </div>
            {decisionOutcome === "modified" && (
              <label className="block text-xs font-medium text-muted-foreground">
                最终执行动作
                <textarea
                  required
                  rows={3}
                  maxLength={3000}
                  value={finalAction}
                  onChange={(event) => setFinalAction(event.target.value)}
                  placeholder="写下负责人确认后的动作，避免后续仍按 Agent 原建议执行"
                  className="mt-1.5 block w-full resize-y rounded-md border border-border bg-background p-3 text-sm leading-5 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                />
              </label>
            )}
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-xs font-medium text-muted-foreground">
                负责人
                <input
                  required
                  maxLength={80}
                  value={owner}
                  onChange={(event) => setOwner(event.target.value)}
                  placeholder="姓名或岗位"
                  className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                />
              </label>
              <label className="text-xs font-medium text-muted-foreground">
                复查日期
                <input
                  required
                  type="date"
                  min={new Date().toISOString().slice(0, 10)}
                  value={reviewDate}
                  onChange={(event) => setReviewDate(event.target.value)}
                  className="mt-1.5 block h-10 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
                />
              </label>
            </div>
            <label className="block text-xs font-medium text-muted-foreground">
              执行备注 <span className="font-normal">可选</span>
              <textarea
                rows={2}
                maxLength={1000}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="补充资源限制、协作方或执行前提"
                className="mt-1.5 block w-full resize-y rounded-md border border-border bg-background p-3 text-sm leading-5 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary"
              />
            </label>
            {saveError && (
              <p className="text-sm text-red-700" role="alert">
                {saveError}
              </p>
            )}
            <button
              type="submit"
              disabled={saving || !owner.trim() || !reviewDate || !rationale.trim() || (decisionOutcome === "modified" && !finalAction.trim()) || !expectedScopeId || !currentMetric || (!auditPassed && decisionOutcome !== "rejected")}
              className="inline-flex h-10 items-center rounded-md bg-foreground px-4 text-sm font-semibold text-background outline-none hover:opacity-90 focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-45"
            >
              {saving ? "正在落档…" : decisionOutcome === "adopted" ? "确认执行并落档" : decisionOutcome === "modified" ? "确认修改并落档" : "记录不采用"}
            </button>
          </form>
        )}
      </section>
      {decision && (
        <OutcomeReview
          conversationId={conversationId}
          decision={decision}
          recordedOutcome={recordedOutcome}
        />
      )}
    </article>
  );
}
