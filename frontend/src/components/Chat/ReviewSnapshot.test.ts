import { describe, expect, it } from "vitest";
import { comparisonRows, decomposeDailyContribution, derivePathBottlenecks, incrementalityBoundary } from "./ReviewSnapshot";
import type { UIMessage } from "@/types";

function sql(rows: Record<string, unknown>[]): UIMessage {
  return { id: "m", event_type: "SQL", role: "assistant", payload: { rows } };
}

describe("comparisonRows", () => {
  it("accepts the two complete period rows", () => {
    const rows = comparisonRows(sql([
      { period: "baseline", days: 7, completed_orders: 10, buyer_users: 9, net_revenue_cents: 1000, merchant_discount_cents: 100, refund_cents: 0, contribution_cents: 600 },
      { period: "activity", days: 7, completed_orders: 12, buyer_users: 11, net_revenue_cents: 900, merchant_discount_cents: 200, refund_cents: 50, contribution_cents: 400 },
    ]));
    expect(rows?.[1].contribution_cents).toBe(400);
  });

  it("refuses partial or malformed data instead of rendering invented values", () => {
    expect(comparisonRows(sql([{ period: "baseline", days: 7 }]))).toBeNull();
    expect(comparisonRows(sql([
      { period: "baseline", days: 7, completed_orders: 10, net_revenue_cents: 1000, contribution_cents: 600 },
      { period: "other", days: 7, completed_orders: 10, net_revenue_cents: 1000, contribution_cents: 600 },
    ]))).toBeNull();
  });
});

it("decomposes daily contribution exactly and rejects undefined unit economics", () => {
  const rows = comparisonRows(sql([
    {period:"baseline",days:7,completed_orders:94,buyer_users:90,net_revenue_cents:694100,merchant_discount_cents:0,refund_cents:0,contribution_cents:393300},
    {period:"activity",days:7,completed_orders:63,buyer_users:63,net_revenue_cents:428000,merchant_discount_cents:0,refund_cents:0,contribution_cents:219100},
  ]));
  expect(rows).not.toBeNull();
  const breakdown = decomposeDailyContribution(rows![0], rows![1]);
  expect(breakdown).not.toBeNull();
  expect(breakdown!.volumeCentsPerDay + breakdown!.unitCentsPerDay).toBeCloseTo(breakdown!.deltaCentsPerDay, 8);
  expect(breakdown!.volumeCentsPerDay).toBeLessThan(0);
  expect(breakdown!.unitCentsPerDay).toBeLessThan(0);
  expect(decomposeDailyContribution({...rows![0],completed_orders:0},rows![1])).toBeNull();
});

it("deterministically locates the lowest adjacent-stage continuation", () => {
  const result = derivePathBottlenecks([{
    variant: "阶段任务", exposed_users: 540, landing_users: 431,
    claim_users: 315, activated_users: 237, buyer_users: 46,
    path_buyer_users: 46, completed_orders: 46,
    net_revenue_cents: 460000, contribution_cents: 120000,
  }]);
  expect(result).toEqual([expect.objectContaining({
    variant: "阶段任务",
    stage_key: "activation_to_path_purchase",
    continuation_rate: 46 / 237,
    not_continued_users: 191,
  })]);
});

it("distinguishes validated conditional incrementality from descriptive review", () => {
  expect(incrementalityBoundary({
    status: "identified",
    detail: "validated",
    estimates: [{
      status: "identified",
      metric: "completed_orders",
      decision_ready: true,
    }],
  })).toEqual(expect.objectContaining({
    identified: true,
    label: "条件性增量已识别",
  }));

  expect(incrementalityBoundary({
    status: "validation_failed",
    detail: "pre-trend failed",
    estimates: [{
      status: "validation_failed",
      metric: "completed_orders",
      decision_ready: false,
    }],
  })).toEqual(expect.objectContaining({
    identified: false,
    label: "描述性复盘",
  }));
});
