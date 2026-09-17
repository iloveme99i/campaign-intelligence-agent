import { useState } from "react";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ToolCallPayload, ToolResultPayload } from "@/types";

interface ToolCallProps {
  payload: ToolCallPayload;
}

interface ToolResultProps {
  payload: ToolResultPayload;
}

export function ToolCallMessage({ payload }: ToolCallProps) {
  const [open, setOpen] = useState(false);
  const dimensionLabels: Record<string, string> = {
    variant: "实验组",
    channel: "渠道",
    location_id: "经营点",
    audience: "目标人群",
  };
  const dimension =
    typeof payload.tool_input.dimension === "string"
      ? dimensionLabels[payload.tool_input.dimension] ?? payload.tool_input.dimension
      : null;
  const reason =
    typeof payload.tool_input.reason === "string"
      ? payload.tool_input.reason
      : null;
  const title =
    payload.tool_name === "compare_periods"
      ? "核对活动总盘"
      : payload.tool_name === "diagnose_dimension" && dimension
        ? `下钻${dimension}`
        : payload.tool_name === "execute_sql"
          ? "查询证据"
          : "读取分析资料";

  return (
    <div className="max-w-[90%]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <Wrench className="w-3.5 h-3.5" />
        <span className="font-medium">{title}</span>
        {reason && <span className="truncate opacity-70">· {reason}</span>}
        {open ? (
          <ChevronDown className="w-3 h-3" />
        ) : (
          <ChevronRight className="w-3 h-3" />
        )}
      </button>
      {open && (
        <pre className="mt-1 px-3 py-2 text-xs bg-muted/50 rounded border border-border overflow-x-auto">
          {JSON.stringify(payload.tool_input, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function ToolResultMessage({ payload }: ToolResultProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="max-w-[90%]">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1.5 text-xs transition-colors ${
          payload.is_error
            ? "text-red-500 hover:text-red-600"
            : "text-muted-foreground hover:text-foreground"
        }`}
      >
        <span className="font-mono">↳ {payload.tool_name}</span>
        {open ? (
          <ChevronDown className="w-3 h-3" />
        ) : (
          <ChevronRight className="w-3 h-3" />
        )}
      </button>
      {open && (
        <pre className="mt-1 px-3 py-2 text-xs bg-muted/50 rounded border border-border overflow-x-auto max-h-48">
          {payload.result}
        </pre>
      )}
    </div>
  );
}
