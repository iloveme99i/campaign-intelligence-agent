import type { SSEEvent } from "@/types";
import type { ReviewScope, ReviewTask } from "@/types/merchant";

export async function* parseSseStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SSEEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";

  const parseBlocks = function* (blocks: string[]): Generator<SSEEvent> {
    for (const block of blocks) {
      const data = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (!data) continue;
      try {
        yield JSON.parse(data) as SSEEvent;
      } catch {
        // Ignore malformed events while keeping the rest of the stream usable.
      }
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      const trailing = buffer.trim();
      if (trailing) yield* parseBlocks([trailing]);
      return;
    }
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    yield* parseBlocks(blocks);
  }
}

export async function* reattachStream(
  conversationId: string,
  signal?: AbortSignal
): AsyncGenerator<SSEEvent> {
  const res = await fetch(`/api/conversations/${conversationId}/stream`, { signal });
  if (!res.ok || res.status === 204 || !res.body) return;

  yield* parseSseStream(res.body);
}

export async function* streamMessage(
  conversationId: string,
  text: string,
  signal?: AbortSignal,
  reviewScope?: ReviewScope,
  reviewTask?: ReviewTask,
): AsyncGenerator<SSEEvent> {
  const res = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      ...(reviewScope ? { review_scope: reviewScope } : {}),
      ...(reviewTask ? { review_task: reviewTask } : {}),
    }),
    signal,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `请求未成功（${res.status}），请检查输入后重试。`);
  }

  yield* parseSseStream(res.body!);
}
