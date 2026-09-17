export interface ReviewScope {
  campaign_id: string;
  baseline: { start: string; end: string };
  activity: { start: string; end: string };
  variant: string | null;
  channel: string | null;
  location_id: string | null;
  audience: string | null;
  refund_basis: "after_refunds" | "before_refunds";
}

export interface ReviewTask {
  decision_intent: "continue" | "adjust" | "scale" | "stop";
  risk_focus: "goal" | "path" | "variant" | "cost";
  business_context: string;
}
