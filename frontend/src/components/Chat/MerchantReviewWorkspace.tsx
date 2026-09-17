import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Clock3, LoaderCircle } from "lucide-react";
import {
  getScopeRevision,
  getTraceQuality,
  type DecisionCommit,
  type DecisionOutcomeRecord,
  type ScopeRevision,
  type TraceQuality,
} from "@/api/merchant";
import type { UIMessage } from "@/types";
import type { ReviewTask } from "@/types/merchant";
import {
  groupIntoTurns,
  latestTurnDecision,
  latestTurnOutcome,
} from "@/lib/groupMessages";
import { AgentWorkBlock } from "./messages/AgentWorkBlock";
import { ReviewSnapshot } from "./ReviewSnapshot";
import { MessageInput } from "./MessageInput";
import { DecisionRecord } from "./DecisionRecord";
import { DiagnosticTrail } from "./DiagnosticTrail";
import { TraceAudit } from "./TraceAudit";
import { ScopeRevisionNotice } from "./ScopeRevisionNotice";

interface Props {
  conversationId: string;
  messages: UIMessage[];
  evidence?: UIMessage;
  isStreaming: boolean;
  printing: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  onConfigureModel: () => void;
}

function errorText(messages: UIMessage[]) {
  const error = [...messages]
    .reverse()
    .find(
      (message) =>
        message.event_type === "ERROR" ||
        (message.event_type === "TOOL_RESULT" &&
          message.payload.is_error === true),
    );
  const text = error?.payload.error ?? error?.payload.result;
  return typeof text === "string" ? text.replace(/^\w+(?:Error)?:\s*/, "") : "";
}

export function MerchantReviewWorkspace({
  conversationId,
  messages,
  evidence,
  isStreaming,
  printing,
  onSend,
  onStop,
  onConfigureModel,
}: Props) {
  const [mobilePane, setMobilePane] = useState<"evidence" | "agent">(
    "evidence",
  );
  const [traceAudit, setTraceAudit] = useState<TraceQuality>();
  const [scopeRevision, setScopeRevision] = useState<ScopeRevision>();
  const [localCommit, setLocalCommit] = useState<{
    answerId: string;
    decision: DecisionCommit;
  }>();
  const turns = groupIntoTurns(messages, isStreaming);
  const latest = turns[turns.length - 1];
  const answer = latest?.finalMsg;
  const answerId = answer?.id;
  const diagnoses = (latest?.workMsgs ?? []).filter(
      (message) =>
        message.event_type === "SQL" &&
        message.payload.tool_name === "diagnose_dimension",
    );
  const persistedDecision = latestTurnDecision(messages);
  const persistedOutcome = latestTurnOutcome(messages);
  const committedDecision =
    (localCommit && localCommit.answerId === answer?.id
      ? localCommit.decision
      : undefined) ??
    (persistedDecision?.payload as DecisionCommit | undefined);
  const reviewTask = [...messages]
    .reverse()
    .find(
      (message) =>
        message.role === "user" &&
        message.event_type === "TEXT" &&
        message.payload.review_task,
    )?.payload.review_task as ReviewTask | undefined;
  const latestError = errorText(latest?.workMsgs ?? []);
  const needsModelConfiguration = /模型尚未配置|模型未连接|API Key/.test(
    latestError,
  );
  const state = isStreaming
    ? "running"
    : latestError
      ? "error"
      : answer
        ? "ready"
        : "empty";

  useEffect(() => {
    if (isStreaming) setMobilePane("agent");
  }, [isStreaming]);

  useEffect(() => {
    setTraceAudit(undefined);
    setScopeRevision(undefined);
    if (!answerId || isStreaming) return;
    let active = true;
    void Promise.allSettled([
      getTraceQuality(conversationId),
      getScopeRevision(conversationId),
    ]).then(([auditResult, revisionResult]) => {
      if (!active) return;
      if (auditResult.status === "fulfilled") setTraceAudit(auditResult.value);
      if (revisionResult.status === "fulfilled")
        setScopeRevision(revisionResult.value);
    });
    return () => {
      active = false;
    };
  }, [answerId, conversationId, isStreaming]);

  return (
    <div className="merchant-review-grid min-h-0 flex-1 overflow-hidden bg-muted/35">
      <nav
        className="merchant-pane-switch border-b border-border bg-background p-1"
        aria-label="复盘工作区"
        data-print-hide
      >
        <button
          type="button"
          aria-pressed={mobilePane === "evidence"}
          onClick={() => setMobilePane("evidence")}
          className={mobilePane === "evidence" ? "is-active" : ""}
        >
          计算证据
        </button>
        <button
          type="button"
          aria-pressed={mobilePane === "agent"}
          onClick={() => setMobilePane("agent")}
          className={mobilePane === "agent" ? "is-active" : ""}
        >
          决策记录
        </button>
      </nav>

      <main
        className={`merchant-evidence-pane min-w-0 overflow-y-auto ${mobilePane === "evidence" ? "is-mobile-active" : ""}`}
        aria-label="复盘证据"
      >
        <ReviewSnapshot message={evidence} diagnoses={diagnoses} />
      </main>

      <aside
        className={`merchant-decision-pane min-h-0 min-w-0 bg-background ${mobilePane === "agent" ? "is-mobile-active" : ""}`}
        aria-labelledby="decision-record-title"
      >
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
          <div className="min-w-0">
            <h2 id="decision-record-title" className="text-sm font-semibold">
              决策记录
            </h2>
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
              结论、依据、风险与下一步
            </p>
          </div>
          <span
            className={`inline-flex items-center gap-1.5 text-xs ${state === "error" ? "text-red-700" : state === "ready" && committedDecision ? "text-emerald-700" : state === "ready" ? "text-amber-700" : "text-muted-foreground"}`}
          >
            {state === "running" && (
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
            )}
            {state === "ready" && committedDecision && (
              <CheckCircle2 className="h-3.5 w-3.5" />
            )}
            {state === "ready" && !committedDecision && (
              <Clock3 className="h-3.5 w-3.5" />
            )}
            {state === "error" && <AlertCircle className="h-3.5 w-3.5" />}
            {state === "running"
              ? "分析中"
              : state === "ready"
                ? committedDecision
                  ? "已落档"
                  : "待确认"
                : state === "error"
                  ? "需要处理"
                  : "待生成"}
          </span>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 md:px-5">
          <DiagnosticTrail diagnoses={diagnoses} complete={Boolean(answer)} />
          <ScopeRevisionNotice revision={scopeRevision} />
          <TraceAudit audit={traceAudit} />
          {latest?.workMsgs.length ? (
            <AgentWorkBlock
              workMessages={latest.workMsgs}
              turnUsage={latest.finalMsg?.turnUsage}
              isStreaming={latest.isActivelyStreaming}
              showReasoning={false}
            />
          ) : null}

          {state === "ready" && answer && (answer.payload as { text?: string }).text?.trim() ? (
            <div className="mt-4 first:mt-0">
              <DecisionRecord
                key={`${conversationId}:${answer.id}`}
                answer={(answer.payload as { text: string }).text}
                evidence={evidence}
                diagnosticEvidence={diagnoses}
                conversationId={conversationId}
                committedDecision={committedDecision}
                recordedOutcome={persistedOutcome?.payload as DecisionOutcomeRecord | undefined}
                onCommitted={(decision) =>
                  setLocalCommit({ answerId: answer.id, decision })
                }
                reviewTask={reviewTask}
                auditPassed={traceAudit?.status === "passed"}
              />
            </div>
          ) : state === "error" ? (
            <div className="mt-4 border-t border-border pt-4">
              <p className="text-sm font-medium text-red-800">
                本轮判断没有生成
              </p>
              <p className="mt-2 text-base leading-7 text-muted-foreground">
                {latestError || "模型调用失败。请检查模型配置后重新计算。"}
              </p>
              <button
                type="button"
                onClick={() => {
                  if (needsModelConfiguration) {
                    onConfigureModel();
                    return;
                  }
                  onSend(
                    "基于当前已确认的复盘口径继续分析，给出事实、解释假设、待验证项和下一轮建议。",
                  );
                }}
                className="mt-4 rounded-lg border border-border px-3 py-2 text-sm font-medium outline-none hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-primary"
              >
                {needsModelConfiguration ? "配置模型" : "重试本轮判断"}
              </button>
            </div>
          ) : (
            <div className="flex min-h-56 items-center">
              <div className="max-w-sm">
                <p className="text-base font-medium">等待确定性核算</p>
                <p className="mt-2 text-base leading-7 text-muted-foreground">
                  确认活动与时间范围后，系统会在同一证据口径下形成结论、
                  风险和下一步验证条件。
                </p>
              </div>
            </div>
          )}
        </div>

        {!printing && (
          <MessageInput
            onSend={onSend}
            onStop={onStop}
            disabled={isStreaming}
            isStreaming={isStreaming}
            compact
          />
        )}
      </aside>
    </div>
  );
}
