import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { TraceQuality } from "@/api/merchant";
import { TraceAudit } from "./TraceAudit";

function audit(overrides: Partial<TraceQuality> = {}): TraceQuality {
  return {
    status: "passed",
    passed: 36,
    total: 36,
    failed_checks: [],
    groups: [
      { key: "scope", label: "口径完整", passed: 5, total: 5 },
      { key: "evidence", label: "证据闭环", passed: 9, total: 9 },
      { key: "diagnosis", label: "诊断克制", passed: 5, total: 5 },
      { key: "decision", label: "决策边界", passed: 17, total: 17 },
    ],
    evidence_count: 14,
    model_calls: 2,
    input_tokens: 100,
    output_tokens: 20,
    total_tokens: 120,
    elapsed_seconds: 10,
    ...overrides,
  };
}

describe("TraceAudit", () => {
  it("keeps the passing state concise", () => {
    const html = renderToStaticMarkup(<TraceAudit audit={audit()} />);
    expect(html).toContain("36/36");
    expect(html).toContain("仍需业务负责人确认");
    expect(html).not.toContain("采用前需要修正");
  });

  it("turns internal failed checks into actionable Chinese reasons", () => {
    const html = renderToStaticMarkup(<TraceAudit audit={audit({
      status: "review_required",
      passed: 34,
      failed_checks: ["money_unit_scale_sanity", "money_values_grounded"],
    })} />);
    expect(html).toContain("采用前需要修正");
    expect(html).toContain("金额单位或数量级异常");
    expect(html).toContain("金额与确定性证据不一致");
    expect(html).toContain("不要直接采用或落档");
    expect(html).not.toContain("money_unit_scale_sanity");
  });

  it("does not describe a partially improved repair as acceptable", () => {
    const html = renderToStaticMarkup(<TraceAudit audit={audit({
      status: "review_required",
      passed: 35,
      failed_checks: ["diagnosis_supports_conclusion"],
      quality_retry: {
        status: "rejected",
        attempt: 1,
        original_passed: 31,
        repaired_passed: 35,
        total: 36,
        remaining_failed_checks: ["diagnosis_supports_conclusion"],
      },
    })} />);

    expect(html).toContain("仍有门禁未通过，未替换");
    expect(html).not.toContain("修正版未改善");
  });

  it("explains the added decision and presentation boundaries without internal keys", () => {
    const html = renderToStaticMarkup(<TraceAudit audit={audit({
      status: "review_required",
      failed_checks: [
        "no_raw_money_units", "decision_intent_contract",
        "no_internal_field_leakage", "incrementality_pretrend_power_clarity",
      ],
    })} />);
    expect(html).toContain("金额未统一换算为元");
    expect(html).toContain("未回答本轮决策问题或说明成立条件");
    expect(html).toContain("前趋势检验的证据强度被高估");
    expect(html).not.toContain("存在未通过的质量检查");
    expect(html).not.toContain("no_raw_money_units");
  });
});
