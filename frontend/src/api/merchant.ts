export interface CampaignSummary {
  campaign_id: string;
  campaign_name: string;
  objective: string;
  start_date: string;
  end_date: string;
  primary_metric:
    | "conversion_rate"
    | "completed_orders"
    | "net_revenue"
    | "roi";
  target_value: string;
  attribution_days: number;
  exposed_users: number;
  completed_orders: number;
  activity_cost_cents: number;
}

export interface SnapshotCatalog {
  synthetic: boolean;
  campaign_count: number;
  event_count: number;
  completed_order_count: number;
  campaigns: CampaignSummary[];
  channels: string[];
  locations: string[];
  audiences: string[];
  variants: string[];
  event_basis: "distinct_user";
  order_basis: "completed";
}

export async function getSnapshotCatalog(
  engineName: string,
  signal?: AbortSignal,
): Promise<SnapshotCatalog> {
  const response = await fetch(
    `/api/merchant/snapshots/${encodeURIComponent(engineName)}/catalog`,
    { signal },
  );
  if (!response.ok)
    throw new Error("无法读取活动数据；这可能是旧版快照，请重新导入。");
  return response.json();
}

export async function createExampleSnapshot(): Promise<{
  engine_name: string;
  row_counts: Record<string, number>;
}> {
  const response = await fetch("/api/merchant/examples", { method: "POST" });
  if (!response.ok) throw new Error("合成示例创建失败，请重试。");
  return response.json();
}

export interface DecisionCommit {
  status: "committed";
  decision_outcome?: "adopted" | "modified" | "rejected";
  reason_code?: "evidence_supported" | "execution_constraint" | "insufficient_evidence" | "risk_too_high" | "priority_changed" | "other";
  rationale?: string;
  final_action?: string;
  owner: string;
  review_date: string;
  note: string;
  scope_id: string;
  answer_sha256: string;
  committed_at: string;
  experiment_plan: ExperimentPlan | null;
  quality_snapshot?: {
    passed: number;
    total: number;
    failed_checks: string[];
  } | null;
}

export interface DecisionOutcomeRecord {
  status: "recorded";
  outcome_id: string;
  decision_committed_at: string;
  scope_id: string;
  observed_on: string;
  implementation_status: "completed" | "partial" | "not_executed";
  measurement_method: "randomized_experiment" | "holdout_comparison" | "before_after";
  randomization_verified: boolean;
  guardrail_status: "passed" | "failed" | "not_measured";
  source_reference: string;
  learning: string;
  next_decision: "scale" | "iterate" | "stop" | "collect_more_data";
  counts: {
    control_total: number;
    control_successes: number;
    treatment_total: number;
    treatment_successes: number;
  } | null;
  evaluation: {
    status: string;
    method: string;
    control_rate: number;
    treatment_rate: number;
    difference_pp: number;
    ci95_difference_pp: [number, number];
    p_value: number;
    sample_check: string;
    allocation_check: string;
    allocation_p_value: number;
    required_total: number | null;
    observed_total: number;
    sample_coverage: number | null;
    causal_readout: boolean;
    conclusion: string;
  } | null;
  recorded_at: string;
}

export interface TraceQuality {
  status: "passed" | "review_required";
  passed: number;
  total: number;
  failed_checks: string[];
  groups: Array<{
    key: "scope" | "evidence" | "diagnosis" | "decision";
    label: string;
    passed: number;
    total: number;
  }>;
  evidence_count: number;
  model_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  elapsed_seconds: number | null;
  quality_retry?: {
    status: "accepted" | "rejected";
    attempt: number;
    original_passed: number;
    repaired_passed: number;
    total: number;
    remaining_failed_checks: string[];
  } | null;
}

export interface ScopeRevision {
  status: "single_scope" | "revised";
  completed_scope_count: number;
  changed_fields: Array<{
    key: string;
    label: string;
    previous: string;
    current: string;
  }>;
  metric_changes: Array<{
    key: string;
    label: string;
    unit: "count" | "cents";
    previous: number;
    current: number;
    delta: number;
    change_rate: number | null;
  }>;
  old_conclusion_superseded: boolean;
  previous_decision_superseded: boolean;
  reason?: string;
}

export interface ExperimentPlan {
  status: "draft_requires_confirmation";
  method: string;
  baseline_rate: number;
  target_rate: number;
  mde_pp: number;
  alpha: number;
  power: number;
  traffic_share: number;
  required_per_group: number;
  required_total: number;
  observed_daily_eligible_users: number;
  estimated_days: number;
  feasibility: {
    status: "exceeds_observed_window" | "within_observed_window";
    reference_window_days: number;
    eligible_users_per_window: number;
    required_windows: number;
    detail: string;
  };
  planning_basis: string;
  population: {
    dimension: "all" | "variant" | "channel" | "location_id" | "audience";
    value: string | null;
    secondary_dimension?: "variant" | "channel" | "location_id" | "audience";
    secondary_value?: string;
    snapshot_id?: string;
    scope_id?: string;
    source?: "recomputed_snapshot_intersection";
    metric:
      | "purchase_per_exposure"
      | "activation_per_claim"
      | "path_purchase_per_activation";
    eligible_users: number;
    successful_users: number;
  };
  readiness: Array<{
    key: string;
    label: string;
    status:
      | "user_confirmed_input"
      | "requires_validation"
      | "requires_business_threshold";
    detail: string;
  }>;
  decision_rule: string;
}

export async function getTraceQuality(
  conversationId: string,
): Promise<TraceQuality> {
  const response = await fetch(
    `/api/merchant/conversations/${encodeURIComponent(conversationId)}/trace-quality`,
  );
  if (!response.ok) throw new Error("无法读取本轮运行审计。");
  return response.json();
}

export async function getScopeRevision(
  conversationId: string,
): Promise<ScopeRevision> {
  const response = await fetch(
    `/api/merchant/conversations/${encodeURIComponent(conversationId)}/scope-revision`,
  );
  if (!response.ok) throw new Error("无法读取口径修订记录。");
  return response.json();
}

export async function planExperiment(
  conversationId: string,
  input: {
    mde_pp: number;
    traffic_share: number;
    expected_scope_id: string;
    expected_answer: string;
    planning_dimension: ExperimentPlan["population"]["dimension"];
    planning_value: string | null;
    planning_secondary_dimension?: ExperimentPlan["population"]["secondary_dimension"] | null;
    planning_secondary_value?: string | null;
    planning_metric: ExperimentPlan["population"]["metric"];
  },
): Promise<ExperimentPlan> {
  const response = await fetch(
    `/api/merchant/conversations/${encodeURIComponent(conversationId)}/experiment-plan`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "实验样本测算失败，请检查输入后重试。",
    );
  }
  return data as ExperimentPlan;
}

export async function commitDecision(
  conversationId: string,
  input: {
    owner: string;
    review_date: string;
    note: string;
    decision_outcome: NonNullable<DecisionCommit["decision_outcome"]>;
    reason_code: NonNullable<DecisionCommit["reason_code"]>;
    rationale: string;
    final_action: string;
    expected_scope_id: string;
    expected_answer: string;
    experiment_mde_pp?: number;
    experiment_traffic_share?: number;
    experiment_dimension?: ExperimentPlan["population"]["dimension"];
    experiment_value?: string | null;
    experiment_secondary_dimension?: ExperimentPlan["population"]["secondary_dimension"] | null;
    experiment_secondary_value?: string | null;
    experiment_metric?: ExperimentPlan["population"]["metric"];
  },
): Promise<DecisionCommit> {
  const response = await fetch(
    `/api/merchant/conversations/${encodeURIComponent(conversationId)}/decisions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  const data = await response.json();
  if (!response.ok) {
    throw new Error(
      typeof data.detail === "string" ? data.detail : "决策落档失败，请重试。",
    );
  }
  return data;
}

export async function recordDecisionOutcome(
  conversationId: string,
  input: {
    observed_on: string;
    implementation_status: DecisionOutcomeRecord["implementation_status"];
    measurement_method: DecisionOutcomeRecord["measurement_method"];
    randomization_verified: boolean;
    control_total?: number;
    control_successes?: number;
    treatment_total?: number;
    treatment_successes?: number;
    guardrail_status: DecisionOutcomeRecord["guardrail_status"];
    source_reference: string;
    learning: string;
    next_decision: DecisionOutcomeRecord["next_decision"];
    expected_decision_committed_at: string;
  },
): Promise<DecisionOutcomeRecord> {
  const response = await fetch(
    `/api/merchant/conversations/${encodeURIComponent(conversationId)}/outcomes`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      typeof data.detail === "string" ? data.detail : "执行结果保存失败，请重试。",
    );
  }
  return data as DecisionOutcomeRecord;
}
