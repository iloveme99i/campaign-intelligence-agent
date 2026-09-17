import { ArrowRight, Check, Search } from "lucide-react";
import type { UIMessage } from "@/types";

const DIMENSION_NAMES: Record<string, string> = {
  variant: "实验组",
  channel: "渠道",
  location_id: "经营点",
  audience: "目标人群",
};

export function DiagnosticTrail({
  diagnoses,
  complete,
}: {
  diagnoses: UIMessage[];
  complete: boolean;
}) {
  if (!diagnoses.length) return null;
  const dimensions = diagnoses.map((diagnosis) =>
    typeof diagnosis.payload.diagnostic_dimension === "string"
      ? DIMENSION_NAMES[diagnosis.payload.diagnostic_dimension] ?? diagnosis.payload.diagnostic_dimension
      : "定向",
  );
  const count = new Set(diagnoses.flatMap((diagnosis) => [
    diagnosis.payload.evidence_id,
    ...(Array.isArray(diagnosis.payload.related_evidence_ids)
      ? diagnosis.payload.related_evidence_ids : []),
  ].filter((id): id is string => typeof id === "string" && id.startsWith("q_")))).size;

  return (
    <section
      className="mb-4 border-y border-border py-4"
      aria-labelledby="diagnostic-trail-title"
    >
      <div className="flex items-center justify-between gap-3">
        <h3
          id="diagnostic-trail-title"
          className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground"
        >
          <Search className="h-3.5 w-3.5" aria-hidden="true" />
          调查路径
        </h3>
        <span className="text-[11px] text-muted-foreground">
          {count} 项新增证据
        </span>
      </div>

      <ol className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <li className="inline-flex min-w-0 items-center gap-1.5 font-medium">
          <Check className="h-3.5 w-3.5 text-emerald-700" aria-hidden="true" />
          总盘核算
        </li>
        <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <li className="inline-flex min-w-0 items-center gap-1.5 font-semibold text-primary">
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
          {dimensions.join("、")}复核
        </li>
        <ArrowRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <li className="inline-flex min-w-0 items-center gap-1.5 font-medium">
          {complete && <Check className="h-3.5 w-3.5 text-emerald-700" aria-hidden="true" />}
          {complete ? "形成判断" : "形成判断中"}
        </li>
      </ol>

      <ul className="mt-3 space-y-1.5 text-sm leading-6 text-foreground">
        {diagnoses.map((diagnosis, index) => <li key={diagnosis.id}>
          <span className="mr-1 font-medium">{dimensions[index]}：</span>
          {typeof diagnosis.payload.diagnostic_reason === "string" ? diagnosis.payload.diagnostic_reason : "复核当前维度读数。"}
        </li>)}
      </ul>
      {complete && (
        <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
          本轮按业务问题选择定向复核；未验证的原因仍作为待验证项。
        </p>
      )}
    </section>
  );
}
