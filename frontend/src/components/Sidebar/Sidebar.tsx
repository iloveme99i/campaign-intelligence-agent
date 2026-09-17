import { useState } from "react";
import { FilePlus2, Search } from "lucide-react";
import { useConversationsStore } from "@/store/conversations";
import { deleteConversation } from "@/api/conversations";
import { ConversationItem } from "./ConversationItem";
import { AppLogo } from "@/components/Brand/AppLogo";

export function Sidebar() {
  const { conversations, activeId, setActiveId, removeConversation } =
    useConversationsStore();

  const [filter, setFilter] = useState("");

  const handleNew = () => {
    setActiveId(null);
    setFilter("");
  };

  const handleDelete = async (id: string) => {
    await deleteConversation(id);
    removeConversation(id);
  };

  const normalizedFilter = filter.trim().toLowerCase();
  const visibleConversations = normalizedFilter
    ? conversations.filter((c) =>
        c.title.toLowerCase().includes(normalizedFilter),
      )
    : conversations;

  return (
    <aside
      className="hidden h-full w-[248px] shrink-0 flex-col border-r border-border bg-[hsl(var(--surface))] md:flex"
      data-print-navigation
    >
      <div className="flex h-16 items-center justify-between px-4">
        <button
          onClick={() => setActiveId(null)}
          className="rounded-lg outline-none hover:opacity-80 focus-visible:ring-2 focus-visible:ring-primary"
          title="返回活动列表"
        >
          <AppLogo />
        </button>
        <button
          onClick={handleNew}
          className="rounded-lg border border-border p-2 text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary"
          title="新建决策记录"
        >
          <FilePlus2 className="h-4 w-4" />
        </button>
      </div>

      <div className="px-3 pb-3">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="搜索活动或记录…"
            className="h-10 w-full rounded-lg border border-border bg-muted/45 pl-8 pr-3 text-sm outline-none placeholder:text-muted-foreground/70 focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary/20"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto border-t border-border px-2 py-3">
        <p className="px-2 pb-2 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
          决策记录
        </p>
        {conversations.length === 0 ? (
          <p className="text-xs text-muted-foreground px-2 py-4 text-center">
            还没有决策记录
          </p>
        ) : visibleConversations.length === 0 ? (
          <p className="text-xs text-muted-foreground px-2 py-4 text-center">
            没有找到匹配的记录
          </p>
        ) : (
          visibleConversations.map((conv) => (
            <ConversationItem
              key={conv.id}
              conversation={conv}
              isActive={conv.id === activeId}
              onSelect={() => setActiveId(conv.id)}
              onDelete={() => handleDelete(conv.id)}
            />
          ))
        )}
      </div>

      <div className="border-t border-border px-4 py-3">
        <p className="text-xs font-medium text-foreground">Campaign Intelligence</p>
        <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
          数据快照只读，结论保留计算证据
        </p>
      </div>
    </aside>
  );
}
