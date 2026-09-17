import { describe, expect, it } from "vitest";

import { parseSseStream } from "./stream";

function chunkedBody(bytes: Uint8Array, cuts: number[]) {
  let start = 0;
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const end of [...cuts, bytes.length]) {
        controller.enqueue(bytes.slice(start, end));
        start = end;
      }
      controller.close();
    },
  });
}

describe("parseSseStream", () => {
  it("preserves Chinese split across UTF-8 chunks and reads a trailing event", async () => {
    const source =
      'data: {"event":"TEXT_MESSAGE_CONTENT","message_id":"1","payload":{"text":"决策记录：继续扩量"}}';
    const bytes = new TextEncoder().encode(source);
    const firstChineseByte = bytes.findIndex((byte) => byte > 127);
    const events = [];

    for await (const event of parseSseStream(
      chunkedBody(bytes, [firstChineseByte + 1, firstChineseByte + 2]),
    )) {
      events.push(event);
    }

    expect(events).toHaveLength(1);
    expect(events[0].payload.text).toBe("决策记录：继续扩量");
  });

  it("supports CRLF framing and multiple data lines", async () => {
    const source =
      'data: {"event":"COMPLETE",\r\ndata: "message_id":"2","payload":{}}\r\n\r\n';
    const events = [];
    for await (const event of parseSseStream(
      chunkedBody(new TextEncoder().encode(source), [17, 31]),
    )) {
      events.push(event);
    }
    expect(events[0].event).toBe("COMPLETE");
  });
});
