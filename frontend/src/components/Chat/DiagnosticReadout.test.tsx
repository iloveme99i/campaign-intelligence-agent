import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { UIMessage } from "@/types";
import { DiagnosticReadout } from "./DiagnosticReadout";

function diagnosis(rows: Record<string, unknown>[]): UIMessage {
  return {
    id: "d1",
    role: "assistant",
    event_type: "SQL",
    payload: {
      tool_name: "diagnose_dimension",
      diagnostic_dimension: "variant",
      evidence_id: "q_diagnostic",
      rows,
    },
  };
}

describe("DiagnosticReadout", () => {
  it("renders a scoped diagnostic comparison without causal claim", () => {
    const html = renderToStaticMarkup(
      <DiagnosticReadout diagnosis={diagnosis([{
        dimension_value: "B", exposed_users: 100, landing_users: 70,
        claim_users: 30, activated_users: 20, buyer_users: 12,
        completed_orders: 12, net_revenue_cents: 120000,
        order_contribution_cents: 30000, conversion_rate: 0.12,
      }])} />,
    );
    expect(html).toContain("诊断证据 q_diagnostic");
    expect(html).toContain("购买/曝光");
    expect(html).toContain("12%");
    expect(html).toContain("不是增量或因果归因");
  });

  it("warns when event counts break a strict funnel interpretation", () => {
    const html = renderToStaticMarkup(
      <DiagnosticReadout diagnosis={diagnosis([{
        dimension_value: "A", exposed_users: 10, landing_users: 12,
        claim_users: 11, activated_users: 5, buyer_users: 12,
        completed_orders: 12, net_revenue_cents: 120000,
        order_contribution_cents: 30000,
      }])} />,
    );
    expect(html).toContain("路径率不能按严格漏斗解释");
  });

  it("suppresses a group purchase rate when order labels differ from prior exposure", () => {
    const html = renderToStaticMarkup(
      <DiagnosticReadout dimensionMismatch={1} diagnosis={diagnosis([{
        dimension_value: "A", exposed_users: 100, landing_users: 70,
        claim_users: 30, activated_users: 20, buyer_users: 12,
        completed_orders: 12, net_revenue_cents: 120000,
        order_contribution_cents: 30000,
      }])} />,
    );
    expect(html).toContain("待核对");
    expect(html).toContain("订单实验组标签与先前曝光不一致");
    expect(html).not.toContain(">12%</td>");
  });

  it("shows the post-action path conversion and its unresolved population", () => {
    const html = renderToStaticMarkup(
      <DiagnosticReadout diagnosis={diagnosis([{
        dimension_value: "B", exposed_users: 400, landing_users: 320,
        claim_users: 280, activated_users: 237, buyer_users: 46,
        path_buyer_users: 46, activated_without_path_purchase_users: 191,
        activation_to_path_purchase_rate: 46 / 237,
        completed_orders: 46, net_revenue_cents: 460000,
        order_contribution_cents: 120000, conversion_rate: 46 / 400,
      }])} />,
    );
    expect(html).toContain("行动→完整购买");
    expect(html).toContain("19.4%");
    expect(html).toContain("完整购买 46 · 未形成 191");
    expect(html).toContain("不等于永久流失");
  });
});
