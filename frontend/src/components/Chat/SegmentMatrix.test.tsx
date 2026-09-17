import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SegmentMatrix } from "./SegmentMatrix";

describe("SegmentMatrix", () => {
  it("withholds a channel purchase rate when channel identity is unlinked", () => {
    const html = renderToStaticMarkup(<SegmentMatrix
      segments={{ channel: [{
        dimension_value: "push", exposed_users: 100, landing_users: 75,
        claim_users: 50, activated_users: 30, buyer_users: 10,
        completed_orders: 10, net_revenue_cents: 100000,
        order_contribution_cents: 40000, conversion_rate: 0.1,
      }] }}
      evidenceIds={{ channel: "q_12345678" }}
      dimensionMismatches={{ channel: 1 }}
    />);
    expect(html).toContain("待核对");
    expect(html).toContain("订单渠道标签与先前曝光不一致");
    expect(html).not.toContain("10%");
  });

  it("surfaces strict path conversion separately from purchase per exposure", () => {
    const html = renderToStaticMarkup(<SegmentMatrix
      segments={{ channel: [{
        dimension_value: "push", exposed_users: 400, landing_users: 320,
        claim_users: 280, activated_users: 237, buyer_users: 46,
        path_buyer_users: 46, activated_without_path_purchase_users: 191,
        activation_to_path_purchase_rate: 46 / 237,
        completed_orders: 46, net_revenue_cents: 460000,
        order_contribution_cents: 120000, conversion_rate: 46 / 400,
      }] }}
      evidenceIds={{ channel: "q_path" }}
    />);
    expect(html).toContain("行动→完整购买");
    expect(html).toContain("19.4%");
    expect(html).toContain("未形成 191");
    expect(html).toContain("不等于永久流失");
  });
});
