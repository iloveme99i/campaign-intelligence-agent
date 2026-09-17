import { describe, expect, it } from "vitest";
import { priorEqualPeriod } from "./dateRange";

describe("priorEqualPeriod", () => {
  it("keeps calendar dates stable regardless of the machine timezone", () => {
    expect(priorEqualPeriod("2026-08-08", "2026-08-14")).toEqual({
      start: "2026-08-01",
      end: "2026-08-07",
    });
  });

  it("handles month and leap-day boundaries", () => {
    expect(priorEqualPeriod("2024-03-01", "2024-03-02")).toEqual({
      start: "2024-02-28",
      end: "2024-02-29",
    });
  });

  it("rejects reversed ranges", () => {
    expect(() => priorEqualPeriod("2026-08-14", "2026-08-08")).toThrow();
  });
});
