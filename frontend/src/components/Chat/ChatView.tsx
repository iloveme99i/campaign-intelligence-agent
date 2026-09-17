import { useEffect, useCallback, useState, useRef } from "react";
import type { MessageRecord, UsagePayload, TurnUsage } from "@/types";
import { streamMessage, reattachStream } from "@/api/stream";
import {
  generateTitle,
  getConversation,
  createConversation,
} from "@/api/conversations";
import { Download, X } from "lucide-react";
import { useConversationsStore } from "@/store/conversations";
import { useDisplayStore } from "@/store/display";
import { MessageList } from "./MessageList";
import { MessageInput } from "./MessageInput";
import { EngineSelector } from "./EngineSelector";
import { MerchantWelcome } from "./MerchantWelcome";
import { ScopeEditor } from "./ScopeEditor";
import { MerchantReviewWorkspace } from "./MerchantReviewWorkspace";
import type { ReviewScope, ReviewTask } from "@/types/merchant";
import { ContextStatusBar } from "./ContextStatusBar";
import type { UIMessage } from "@/types";
import { buildUiMessages } from "@/lib/buildUiMessages";
import { v4 as uuidv4 } from "uuid";

export function ChatView({ onOpenSettings }: { onOpenSettings: () => void }) {
  const {
    activeId,
    messages,
    engines,
    isStreaming,
    setMessages,
    setActiveId,
    addConversation,
    appendMessage,
    appendStreamingText,
    resetStreamingText,
    markCurrentAsThinking,
    setStreaming,
    updateConversationTitle,
    conversations,
    usageTotals,
    setUsageTotals,
    addUsage,
    attachUsageToMessage,
    setFinalMsgTurnUsage,
    finalizeStreaming,
  } = useConversationsStore();

  const activeConv = conversations.find((c) => c.id === activeId);
  const pendingFirstMessage = useRef<{
    text: string;
    scope: ReviewScope;
    task: ReviewTask;
  } | null>(null);
  const chartErrorRetried = useRef(false);
  const streamAbortRef = useRef<AbortController | null>(null);

  // Load conversation history when activeId changes; fire pending first message
  useEffect(() => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    if (!activeId) {
      setMessages([]);
      return;
    }
    chartErrorRetried.current = false;
    // New conversation from welcome screen — skip history fetch (nothing to load)
    // and fire the first message directly. getConversation would race and overwrite it.
    if (pendingFirstMessage.current) {
      const pending = pendingFirstMessage.current;
      pendingFirstMessage.current = null;
      handleSend(pending.text, pending.scope, pending.task);
      return;
    }

    const snapId = activeId;

    getConversation(snapId).then(async (detail) => {
      // Bail if the user has already switched to a different conversation
      if (activeId !== snapId) return;

      if (!detail.is_streaming) {
        const { messages: uiMsgs, totals } = buildUiMessages(detail.messages);
        setMessages(uiMsgs);
        setUsageTotals(totals);
        return;
      }

      // There is an in-progress agent stream for this conversation.
      // Strip the incomplete in-progress turn (everything after the last user
      // message) — the stream replay will provide those events live.
      const lastUserIdx = detail.messages.reduce(
        (acc, m, i) => (m.role === "user" ? i : acc),
        -1,
      );
      const previousMessages =
        lastUserIdx >= 0
          ? detail.messages.slice(0, lastUserIdx + 1)
          : detail.messages;
      const { messages: prevUiMsgs, totals: prevTotals } =
        buildUiMessages(previousMessages);
      setMessages(prevUiMsgs);
      setUsageTotals(prevTotals);

      setStreaming(true);
      resetStreamingText();
      const controller = new AbortController();
      streamAbortRef.current = controller;

      let aborted = false;
      try {
        const stream = reattachStream(snapId, controller.signal);
        let result = await stream.next();
        let pendingModelUsage: UsagePayload | null = null;
        const reattachTurnUsage: TurnUsage = {
          input_tokens: 0,
          output_tokens: 0,
          total_tokens: 0,
          cache_read_tokens: 0,
          cache_creation_tokens: 0,
          calls: 0,
        };
        while (!result.done) {
          // Bail if the user switched away while we were awaiting
          if (activeId !== snapId) {
            controller.abort();
            return;
          }
          const event = result.value;
          if (event.event === "TEXT") {
            appendStreamingText((event.payload as { text: string }).text);
            const textId = useConversationsStore.getState().streamingTextId;
            if (pendingModelUsage && textId) {
              attachUsageToMessage(textId, pendingModelUsage);
              pendingModelUsage = null;
            }
          } else if (event.event === "TOOL_CALL") {
            markCurrentAsThinking();
            appendMessage({
              id: event.message_id,
              event_type: event.event,
              role: "assistant",
              payload: event.payload,
            });
            if (pendingModelUsage) {
              attachUsageToMessage(event.message_id, pendingModelUsage);
              pendingModelUsage = null;
            }
          } else if (event.event === "USAGE") {
            const usage = event.payload as unknown as UsagePayload;
            addUsage(usage);
            reattachTurnUsage.input_tokens += usage.input_tokens || 0;
            reattachTurnUsage.output_tokens += usage.output_tokens || 0;
            reattachTurnUsage.total_tokens += usage.total_tokens || 0;
            reattachTurnUsage.cache_read_tokens += usage.cache_read_tokens || 0;
            reattachTurnUsage.cache_creation_tokens +=
              usage.cache_creation_tokens || 0;
            reattachTurnUsage.calls += 1;
            if (usage.model) reattachTurnUsage.model = usage.model;
            if (usage.provider) reattachTurnUsage.provider = usage.provider;
            const state = useConversationsStore.getState();
            const targetId = state.streamingTextId;
            if (targetId) attachUsageToMessage(targetId, usage);
            else pendingModelUsage = usage;
          } else if (event.event === "COMPLETE") {
            if (reattachTurnUsage.calls > 0)
              setFinalMsgTurnUsage({ ...reattachTurnUsage });
          } else {
            appendMessage({
              id: event.message_id,
              event_type: event.event,
              role: "assistant",
              payload: event.payload,
            });
          }
          result = await stream.next();
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          aborted = true;
          return;
        }
        appendMessage({
          id: uuidv4(),
          event_type: "ERROR",
          role: "assistant",
          payload: { error: String(err) },
        });
      } finally {
        streamAbortRef.current = null;
        setStreaming(false);
        if (!aborted && activeId === snapId) {
          // Reload full history from DB to catch anything committed after the
          // initial fetch, then generate/update the title.
          getConversation(snapId)
            .then((refreshed) => {
              if (activeId !== snapId) return;
              const { messages: uiMsgs, totals } = buildUiMessages(
                refreshed.messages,
              );
              setMessages(uiMsgs);
              setUsageTotals(totals);
            })
            .catch(() => {});
          generateTitle(snapId)
            .then((r) => {
              if (r.updated) updateConversationTitle(snapId, r.title);
            })
            .catch(() => {});
        }
      }
    });
    // Other references (setMessages, appendMessage, etc.) are stable Zustand store
    // actions — their identity never changes, so only activeId needs to re-trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  const [showReasoning, setShowReasoning] = useState(false);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const [exportIncludeReasoning, setExportIncludeReasoning] = useState(false);
  // While true, MessageList renders the full transcript unvirtualized so
  // window.print() captures every turn (not just the on-screen ones).
  const [printing, setPrinting] = useState(false);

  const handleExport = useCallback(() => {
    // Switch MessageList out of virtualization first, then wait two frames so
    // the full transcript is painted before we snapshot it for print.
    setPrinting(true);
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        document.querySelectorAll("[data-print-expand]").forEach((el) => {
          (el as HTMLElement).style.maxHeight = "none";
          (el as HTMLElement).style.overflow = "visible";
          (el as HTMLElement).style.opacity = "1";
        });

        const slugify = (s: string, fallback: string) => {
          const slug = s
            .normalize("NFKC")
            .toLowerCase()
            .replace(/[^\p{L}\p{N}]+/gu, "-")
            .replace(/^-+|-+$/g, "");
          return slug || fallback;
        };

        const appName = slugify(
          useDisplayStore.getState().appName || "campaign-intelligence",
          "campaign-intelligence",
        );
        const title = slugify(
          activeConv?.title ?? "campaign-review",
          "campaign-review",
        );
        const ts = new Date()
          .toISOString()
          .slice(0, 16)
          .replace("T", "-")
          .replace(":", "");
        const filename = `${appName}-${title}-${ts}`;

        const prev = document.title;
        document.title = filename;
        window.print();
        document.title = prev;
        setPrinting(false);
        setExportModalOpen(false);
      }),
    );
  }, [activeConv?.title]);

  const handleSend = async (
    text: string,
    reviewScope?: ReviewScope,
    reviewTask?: ReviewTask,
  ) => {
    if (!activeId || isStreaming) return;

    // Append user message immediately
    appendMessage({
      id: uuidv4(),
      event_type: "TEXT",
      role: "user",
      payload: {
        text,
        ...(reviewScope ? { review_scope: reviewScope } : {}),
        ...(reviewTask ? { review_task: reviewTask } : {}),
      },
    });

    setStreaming(true);
    resetStreamingText(); // new turn — reset so TEXT goes to a fresh message

    const conversationId = activeId;
    let aborted = false;
    try {
      const controller = new AbortController();
      streamAbortRef.current = controller;
      const stream = streamMessage(
        conversationId,
        text,
        controller.signal,
        reviewScope,
        reviewTask,
      );
      let result = await stream.next();
      let pendingModelUsage: UsagePayload | null = null;
      const sendTurnUsage: TurnUsage = {
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
        cache_read_tokens: 0,
        cache_creation_tokens: 0,
        calls: 0,
      };
      while (!result.done) {
        const event = result.value;
        if (event.event === "TEXT") {
          appendStreamingText((event.payload as { text: string }).text);
          const textId = useConversationsStore.getState().streamingTextId;
          if (pendingModelUsage && textId) {
            attachUsageToMessage(textId, pendingModelUsage);
            pendingModelUsage = null;
          }
        } else if (event.event === "TOOL_CALL") {
          // Text before this tool call was reasoning — mark it as a thinking block
          markCurrentAsThinking();
          appendMessage({
            id: event.message_id,
            event_type: event.event,
            role: "assistant",
            payload: event.payload,
          });
          if (pendingModelUsage) {
            attachUsageToMessage(event.message_id, pendingModelUsage);
            pendingModelUsage = null;
          }
        } else if (event.event === "USAGE") {
          const usage = event.payload as unknown as UsagePayload;
          addUsage(usage);
          sendTurnUsage.input_tokens += usage.input_tokens || 0;
          sendTurnUsage.output_tokens += usage.output_tokens || 0;
          sendTurnUsage.total_tokens += usage.total_tokens || 0;
          sendTurnUsage.cache_read_tokens += usage.cache_read_tokens || 0;
          sendTurnUsage.cache_creation_tokens +=
            usage.cache_creation_tokens || 0;
          sendTurnUsage.calls += 1;
          if (usage.model) sendTurnUsage.model = usage.model;
          if (usage.provider) sendTurnUsage.provider = usage.provider;
          // Tool-only model usage belongs to the following tool, not a prior query.
          const state = useConversationsStore.getState();
          const targetId = state.streamingTextId;
          if (targetId) attachUsageToMessage(targetId, usage);
          else pendingModelUsage = usage;
        } else if (event.event === "COMPLETE") {
          if (sendTurnUsage.calls > 0)
            setFinalMsgTurnUsage({ ...sendTurnUsage });
        } else {
          appendMessage({
            id: event.message_id,
            event_type: event.event,
            role: "assistant",
            payload: event.payload,
          });
        }
        result = await stream.next();
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        aborted = true;
        return;
      }
      appendMessage({
        id: uuidv4(),
        event_type: "ERROR",
        role: "assistant",
        payload: { error: String(err) },
      });
    } finally {
      streamAbortRef.current = null;
      finalizeStreaming();
      setStreaming(false);
      if (!aborted) {
        // Replace optimistic events with the persisted source of truth. This
        // restores server-generated scope/task IDs and any terminal events
        // committed after the final streamed chunk.
        getConversation(conversationId)
          .then((refreshed) => {
            if (useConversationsStore.getState().activeId !== conversationId)
              return;
            const { messages: uiMsgs, totals } = buildUiMessages(
              refreshed.messages,
            );
            setMessages(uiMsgs);
            setUsageTotals(totals);
          })
          .catch(() => {});
        // Fire-and-forget title generation after the turn completes
        generateTitle(conversationId)
          .then((r) => {
            if (r.updated) updateConversationTitle(conversationId, r.title);
          })
          .catch(() => {});
      }
    }
  };

  const handleWelcomeSend = async (
    text: string,
    engineName: string,
    scope: ReviewScope,
    task: ReviewTask,
  ) => {
    const campaignName = text.match(/「([^」]+)」/)?.[1] ?? "活动";
    const decisionLabel = {
      continue: "继续判断",
      adjust: "调整判断",
      scale: "扩量判断",
      stop: "止损判断",
    }[task.decision_intent];
    const conv = await createConversation(
      engineName,
      `${campaignName}｜${decisionLabel}`,
    );
    pendingFirstMessage.current = { text, scope, task };
    addConversation(conv);
    setActiveId(conv.id);
  };

  if (!activeId) {
    return <MerchantWelcome onSend={handleWelcomeSend} />;
  }

  return (
    <div
      className="flex-1 flex flex-col h-full overflow-hidden"
      data-print-chat
    >
      {/* Hidden print header — visible only when printing */}
      <div id="print-header" style={{ display: "none" }}>
        <h1 style={{ fontSize: "18px", fontWeight: 700, margin: 0 }}>
          {activeConv?.title ?? "活动决策记录"}
        </h1>
        <p style={{ fontSize: "12px", color: "#6b7280", margin: "4px 0 0" }}>
          数据快照：{activeConv?.engine_name} · 导出时间：
          {new Date().toLocaleString("zh-CN")}
        </p>
      </div>

      {/* Header */}
      <div
        className="flex min-h-14 items-center justify-between border-b border-border bg-[hsl(var(--surface))] px-5 md:px-7"
        data-print-hide
      >
        <div className="min-w-0">
          <span className="block truncate text-sm font-semibold">
            {activeConv?.title ?? "活动决策记录"}
          </span>
          <span className="mt-0.5 block text-[11px] text-muted-foreground">
            范围、证据与经营决策
          </span>
        </div>
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button
              onClick={() => setExportModalOpen(true)}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs text-muted-foreground outline-none hover:bg-muted/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary"
              title="导出决策记录"
            >
              <Download className="w-3.5 h-3.5" />
              导出记录
            </button>
          )}
          {!activeConv?.engine_name.startsWith("merchant_") && (
            <EngineSelector
              engines={engines}
              selected={activeConv?.engine_name ?? ""}
              onChange={async (name) => {
                if (!activeId || name === activeConv?.engine_name) return;
                await fetch(`/api/conversations/${activeId}/engine`, {
                  method: "PATCH",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ engine_name: name }),
                });
                // Update local store
                useConversationsStore.setState((s) => ({
                  conversations: s.conversations.map((c) =>
                    c.id === activeId ? { ...c, engine_name: name } : c,
                  ),
                }));
              }}
              disabled={isStreaming}
            />
          )}
        </div>
      </div>

      {activeConv?.engine_name.startsWith("merchant_") &&
        (() => {
          let lastUserIndex = -1;
          for (let index = messages.length - 1; index >= 0; index -= 1) {
            if (messages[index].role === "user" && messages[index].event_type === "TEXT") {
              lastUserIndex = index;
              break;
            }
          }
          const last = messages
            .slice(lastUserIndex + 1)
            .reverse()
            .find(
              (message) =>
                message.event_type === "SQL" &&
                message.payload.scope_confirmed === true,
            );
          const latestUserScope =
            lastUserIndex >= 0
              ? (messages[lastUserIndex].payload.review_scope as ReviewScope | undefined)
              : undefined;
          const confirmedScope =
            latestUserScope ?? (last?.payload.scope as ReviewScope | undefined);
          return (
            <>
              <ScopeEditor
                key={`${activeId}:${last?.payload.scope_id ?? messages[lastUserIndex]?.payload.scope_id ?? "new"}`}
                engineName={activeConv.engine_name}
                confirmed={confirmedScope}
                disabled={isStreaming}
                onConfirm={(scope) => {
                  void handleSend(
                    "请按我本轮确认的口径重新复盘，说明与上一轮口径及结论的差异。",
                    scope,
                  );
                }}
              />
              <MerchantReviewWorkspace
                conversationId={activeId}
                messages={messages}
                evidence={last}
                isStreaming={isStreaming}
                printing={printing}
                onSend={handleSend}
                onConfigureModel={onOpenSettings}
                onStop={() => {
                  streamAbortRef.current?.abort();
                  finalizeStreaming();
                  setStreaming(false);
                }}
              />
            </>
          );
        })()}
      {!activeConv?.engine_name.startsWith("merchant_") && (
        <MessageList
          messages={messages}
          isStreaming={isStreaming}
          showReasoning={showReasoning}
          printing={printing}
          onChartError={(error) => {
            if (chartErrorRetried.current || isStreaming) return;
            chartErrorRetried.current = true;
            handleSend(
              `The chart failed to render with this error: "${error}". ` +
                `Please fix the Vega-Lite spec and regenerate the chart. ` +
                `Remember: use "mark": "bar" for horizontal bars — "barh" is not a valid Vega-Lite mark type.`,
            );
          }}
        />
      )}
      {!activeConv?.engine_name.startsWith("merchant_") && (
        <MessageInput
          onSend={handleSend}
          disabled={isStreaming}
          isStreaming={isStreaming}
          onStop={() => {
            streamAbortRef.current?.abort();
            finalizeStreaming();
            setStreaming(false);
          }}
        />
      )}
      {!activeConv?.engine_name.startsWith("merchant_") && (
        <ContextStatusBar
          conversationId={activeId}
          isStreaming={isStreaming}
          messageCount={messages.length}
        />
      )}

      {/* Export options */}
      {exportModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setExportModalOpen(false)}
        >
          <div
            className="bg-background border border-border rounded-xl shadow-xl w-80 p-5 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">导出决策记录</h2>
              <button
                aria-label="关闭导出设置"
                onClick={() => setExportModalOpen(false)}
                className="rounded text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {activeConv?.engine_name.startsWith("merchant_") ? (
              <p className="text-sm leading-6 text-muted-foreground">
                PDF 包含本轮任务、确定性计算、证据附录和最终决策记录；
                不包含模型内部推理过程。
              </p>
            ) : (
              <div className="space-y-3">
                <label className="flex cursor-pointer items-center justify-between">
                  <span className="text-sm text-muted-foreground">
                    包含 Agent 推理过程
                  </span>
                  <button
                    onClick={() => {
                      const next = !exportIncludeReasoning;
                      setExportIncludeReasoning(next);
                      setShowReasoning(next);
                    }}
                    role="switch"
                    aria-checked={exportIncludeReasoning}
                    className={`relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors duration-200 focus:outline-none ${
                      exportIncludeReasoning
                        ? "bg-primary"
                        : "bg-muted-foreground/30"
                    }`}
                  >
                    <span
                      className={`mt-0.5 inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform duration-200 ${
                        exportIncludeReasoning
                          ? "translate-x-4"
                          : "translate-x-0.5"
                      }`}
                    />
                  </button>
                </label>
              </div>
            )}

            <button
              onClick={handleExport}
              className="w-full flex items-center justify-center gap-2 text-sm px-4 py-2 rounded-lg
                         bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              <Download className="w-4 h-4" />
              导出 PDF
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
