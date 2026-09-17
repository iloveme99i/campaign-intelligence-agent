import type { MessageRecord, UIMessage, UsagePayload, TurnUsage } from "@/types";

export function buildUiMessages(records: MessageRecord[]): {
  messages: UIMessage[];
  totals: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    cache_read_tokens: number;
    cache_creation_tokens: number;
    calls: number;
    model?: string;
    provider?: string;
  };
} {
  const result: UIMessage[] = [];
  const totals: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    cache_read_tokens: number;
    cache_creation_tokens: number;
    calls: number;
    model?: string;
    provider?: string;
  } = {
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    calls: 0,
  };

  let pendingTextChunks: { id: string; text: string; created_at?: string }[] = [];
  let completeText = "";
  let seenToolCallAfterText = false;
  let turnUsages: UsagePayload[] = [];
  // USAGE arrives at on_chat_model_end — BEFORE the next TOOL_CALL or COMPLETE
  // flushes the pending text. Stash it here and attach to the message pushed by
  // the next flushText() call (the thinking/response from the same LLM call).
  let pendingUsage: UsagePayload | null = null;

  const flushText = (asThinking: boolean) => {
    if (pendingTextChunks.length === 0) return;
    const merged = pendingTextChunks.map((c) => c.text).join("");
    const finalText = !asThinking && completeText ? completeText : merged;
    if (finalText.trim()) {
      const msg: UIMessage = {
        id: pendingTextChunks[0].id,
        event_type: "TEXT",
        role: "assistant",
        payload: { text: finalText },
        isThinking: asThinking,
        created_at: pendingTextChunks[0].created_at,
      };
      if (pendingUsage) {
        msg.usage = pendingUsage;
        pendingUsage = null;
      }
      result.push(msg);
    }
    pendingTextChunks = [];
    completeText = "";
    seenToolCallAfterText = false;
  };

  for (const m of records) {
    if (m.role === "user" && m.event_type === "TEXT") {
      flushText(seenToolCallAfterText);
      turnUsages = [];
      pendingUsage = null;
      result.push({ id: m.id, event_type: m.event_type, role: "user", payload: m.payload, created_at: m.created_at });
      continue;
    }

    switch (m.event_type) {
      case "TEXT":
        pendingTextChunks.push({ id: m.id, text: (m.payload.text as string) || "", created_at: m.created_at });
        break;

      case "COMPLETE":
        completeText = (m.payload.text as string) || "";
        if (m.payload.quality_retry === true && completeText.trim()) {
          // A server-side quality pass may replace the first answer once. Keep
          // one canonical response in the turn instead of showing two bubbles.
          flushText(false);
          let priorFinal = -1;
          for (let index = result.length - 1; index >= 0; index -= 1) {
            const item = result[index];
            if (item.role === "user") break;
            if (item.role === "assistant" && item.event_type === "TEXT" && !item.isThinking) {
              priorFinal = index;
              break;
            }
          }
          if (priorFinal >= 0) {
            result[priorFinal] = {
              ...result[priorFinal],
              id: m.id,
              payload: { text: completeText, quality_retry: true },
              created_at: m.created_at,
            };
          }
          completeText = "";
        } else if (pendingTextChunks.length > 0) {
          flushText(false);
        } else if (completeText.trim()) {
          result.push({
            id: m.id,
            event_type: "TEXT",
            role: "assistant",
            payload: { text: completeText },
            created_at: m.created_at,
            ...(pendingUsage ? { usage: pendingUsage } : {}),
          });
          pendingUsage = null;
          completeText = "";
        }
        if (turnUsages.length > 0 && result.length > 0) {
          const last = result[result.length - 1];
          if (last.role === "assistant" && last.event_type === "TEXT" && !last.isThinking) {
            const tu: TurnUsage = {
              input_tokens: 0, output_tokens: 0, total_tokens: 0,
              cache_read_tokens: 0, cache_creation_tokens: 0,
              calls: turnUsages.length,
            };
            for (const u of turnUsages) {
              tu.input_tokens += u.input_tokens || 0;
              tu.output_tokens += u.output_tokens || 0;
              tu.total_tokens += u.total_tokens || 0;
              tu.cache_read_tokens += u.cache_read_tokens || 0;
              tu.cache_creation_tokens += u.cache_creation_tokens || 0;
              if (u.model) tu.model = u.model;
              if (u.provider) tu.provider = u.provider;
            }
            if (last.turnUsage) {
              last.turnUsage = {
                ...tu,
                input_tokens: last.turnUsage.input_tokens + tu.input_tokens,
                output_tokens: last.turnUsage.output_tokens + tu.output_tokens,
                total_tokens: last.turnUsage.total_tokens + tu.total_tokens,
                cache_read_tokens: last.turnUsage.cache_read_tokens + tu.cache_read_tokens,
                cache_creation_tokens:
                  last.turnUsage.cache_creation_tokens + tu.cache_creation_tokens,
                calls: last.turnUsage.calls + tu.calls,
                model: tu.model ?? last.turnUsage.model,
                provider: tu.provider ?? last.turnUsage.provider,
              };
            } else {
              last.turnUsage = tu;
            }
          }
        }
        turnUsages = [];
        break;

      case "TOOL_CALL":
        flushText(true);
        seenToolCallAfterText = true;
        result.push({ id: m.id, event_type: "TOOL_CALL", role: "assistant", payload: m.payload, created_at: m.created_at,
          ...(pendingUsage ? { usage: pendingUsage } : {}),
        });
        pendingUsage = null;
        break;

      case "TOOL_RESULT":
      case "SQL":
      case "CHART":
      case "ERROR":
      case "DECISION":
      case "OUTCOME":
        result.push({ id: m.id, event_type: m.event_type, role: "assistant", payload: m.payload, created_at: m.created_at });
        break;

      case "QUALITY_RETRY":
        // Kept in the durable trace for auditability. The user-facing trace
        // audit presents the result; the chat transcript stays focused.
        break;

      case "USAGE": {
        const u = m.payload as unknown as UsagePayload;
        totals.input_tokens += u.input_tokens || 0;
        totals.output_tokens += u.output_tokens || 0;
        totals.total_tokens += u.total_tokens || 0;
        totals.cache_read_tokens += u.cache_read_tokens || 0;
        totals.cache_creation_tokens += u.cache_creation_tokens || 0;
        totals.calls += 1;
        if (u.model) totals.model = u.model;
        if (u.provider) totals.provider = u.provider;
        turnUsages.push(u);
        // If we have buffered TEXT chunks, the USAGE belongs to them — they'll
        // be flushed as a thinking/response message on the next TOOL_CALL or
        // COMPLETE. Stash and attach at flush time.
        if (pendingTextChunks.length > 0) {
          pendingUsage = u;
          break;
        }
        // Model-end precedes tool-start. With no text, reserve this usage for
        // the next tool call rather than assigning it to a previous query.
        pendingUsage = u;
        break;
      }

      default:
        break;
    }
  }

  flushText(seenToolCallAfterText);
  return { messages: result, totals };
}
