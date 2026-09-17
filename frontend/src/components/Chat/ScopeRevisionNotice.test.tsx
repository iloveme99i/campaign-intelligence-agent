import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ScopeRevisionNotice } from "./ScopeRevisionNotice";

describe("ScopeRevisionNotice", () => {
  it("renders business changes without internal scope identifiers", () => {
    const html = renderToStaticMarkup(
      <ScopeRevisionNotice
        revision={{
          status: "revised",
          completed_scope_count: 2,
          changed_fields: [
            { key: "channel", label: "渠道", previous: "全部", current: "直播" },
          ],
          metric_changes: [
            {
              key: "completed_orders",
              label: "完成订单",
              unit: "count",
              previous: 100,
              current: 80,
              delta: -20,
              change_rate: -0.2,
            },
          ],
          old_conclusion_superseded: true,
          previous_decision_superseded: true,
          reason: "复盘口径发生变化，本轮已重新计算。",
        }}
      />,
    );

    expect(html).toContain("口径已修订 · 旧结论已失效");
    expect(html).toContain("直播");
    expect(html).not.toContain("scope_id");
  });

  it("stays hidden for a single completed scope", () => {
    const html = renderToStaticMarkup(
      <ScopeRevisionNotice
        revision={{
          status: "single_scope",
          completed_scope_count: 1,
          changed_fields: [],
          metric_changes: [],
          old_conclusion_superseded: false,
          previous_decision_superseded: false,
        }}
      />,
    );
    expect(html).toBe("");
  });
});
