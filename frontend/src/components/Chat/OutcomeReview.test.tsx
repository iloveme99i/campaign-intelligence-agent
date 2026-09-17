import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { DecisionCommit, DecisionOutcomeRecord } from "@/api/merchant";
import { OutcomeReview } from "./OutcomeReview";

const decision: DecisionCommit = {
  status: "committed",
  decision_outcome: "modified",
  owner: "增长运营",
  review_date: "2026-09-25",
  note: "",
  scope_id: "scope-1",
  answer_sha256: "a".repeat(64),
  committed_at: "2026-09-15T00:00:00Z",
  experiment_plan: null,
};

const outcome: DecisionOutcomeRecord = {
  status: "recorded",
  outcome_id: "outcome-1",
  decision_committed_at: decision.committed_at,
  scope_id: decision.scope_id,
  observed_on: "2026-09-25",
  implementation_status: "completed",
  measurement_method: "randomized_experiment",
  randomization_verified: true,
  guardrail_status: "passed",
  source_reference: "实验平台 EXP-0915",
  learning: "处理组达到预设门槛。",
  next_decision: "scale",
  counts: {
    control_total: 10_000,
    control_successes: 500,
    treatment_total: 10_000,
    treatment_successes: 650,
  },
  evaluation: {
    status: "decision_threshold_met",
    method: "双侧两比例 z 检验",
    control_rate: 0.05,
    treatment_rate: 0.065,
    difference_pp: 1.5,
    ci95_difference_pp: [0.8, 2.2],
    p_value: 0.001,
    sample_check: "ok",
    allocation_check: "ok",
    allocation_p_value: 1,
    required_total: 18_000,
    observed_total: 20_000,
    sample_coverage: 20_000 / 18_000,
    causal_readout: true,
    conclusion: "达到预设最小业务提升。",
  },
  recorded_at: "2026-09-25T12:00:00Z",
};

describe("OutcomeReview", () => {
  it("shows the measured effect, validity and next decision", () => {
    const html = renderToStaticMarkup(
      <OutcomeReview
        conversationId="conversation-1"
        decision={decision}
        recordedOutcome={outcome}
      />,
    );

    expect(html).toContain("达到决策门槛");
    expect(html).toContain("+1.50 pp");
    expect(html).toContain("因果判读");
    expect(html).toContain("满足条件");
    expect(html).toContain("扩大执行");
  });

  it("does not render an execution form for a rejected recommendation", () => {
    const html = renderToStaticMarkup(
      <OutcomeReview
        conversationId="conversation-1"
        decision={{ ...decision, decision_outcome: "rejected" }}
      />,
    );

    expect(html).toBe("");
  });
});
