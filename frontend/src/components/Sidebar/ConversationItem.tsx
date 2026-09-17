import { FileText, Trash2 } from "lucide-react";
import type { ConversationSummary } from "@/types";

interface Props {
  conversation: ConversationSummary;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
}

export function ConversationItem({ conversation, isActive, onSelect, onDelete }: Props) {
  return (
    <div
      className={`group flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2.5 transition-colors ${
        isActive
          ? "bg-secondary text-secondary-foreground"
          : "hover:bg-muted text-foreground"
      }`}
      data-testid="conversation-item"
      data-conv-id={conversation.id}
      onClick={onSelect}
    >
      <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.7} />
      <div className="min-w-0 flex-1">
        <p
          className="line-clamp-2 break-words text-sm font-medium leading-5"
          title={conversation.title}
        >
          {conversation.title}
        </p>
        <p className="mt-0.5 truncate text-[11px] text-muted-foreground">活动决策记录</p>
      </div>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        aria-label={`删除${conversation.title}`}
        className="rounded p-1 text-muted-foreground opacity-0 outline-none hover:bg-red-500/10 hover:text-red-600 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-primary group-hover:opacity-100"
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
