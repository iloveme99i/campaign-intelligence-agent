import { useState, useRef, type KeyboardEvent, type ChangeEvent } from "react";
import { Send, Square } from "lucide-react";

interface Props {
  onSend: (text: string) => void;
  onStop?: () => void;
  disabled: boolean;
  isStreaming?: boolean;
  compact?: boolean;
}

const SLASH_COMMANDS = [
  {
    command: "/improve-context",
    description:
      "Analyze this conversation and propose documentation improvements",
  },
];

export function MessageInput({
  onSend,
  onStop,
  disabled,
  isStreaming,
  compact = false,
}: Props) {
  const [text, setText] = useState("");
  const [showCommands, setShowCommands] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    setShowCommands(false);
    onSend(trimmed);
    setText("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    }
  };

  const handleChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    setShowCommands(val.startsWith("/") && !val.includes(" "));
  };

  const filteredCommands = SLASH_COMMANDS.filter((c) =>
    c.command.startsWith(text.toLowerCase()),
  );

  return (
    <div
      className={`border-t border-border bg-[hsl(var(--surface))] ${compact ? "px-4 pb-4 pt-3" : "px-4 pb-3 pt-3 md:px-7"}`}
      data-print-hide
    >
      {showCommands && filteredCommands.length > 0 && (
        <div className="mb-2 rounded-lg border border-border bg-background shadow-md overflow-hidden">
          {filteredCommands.map((cmd) => (
            <button
              key={cmd.command}
              type="button"
              onClick={() => {
                setText(cmd.command);
                setShowCommands(false);
                textareaRef.current?.focus();
              }}
              className="w-full flex items-baseline gap-2 px-3 py-2 text-left hover:bg-muted transition-colors"
            >
              <span className="text-sm font-mono font-medium text-primary">
                {cmd.command}
              </span>
              <span className="text-xs text-muted-foreground">
                {cmd.description}
              </span>
            </button>
          ))}
        </div>
      )}
      <div
        className={`${compact ? "w-full" : "mx-auto max-w-4xl"} flex items-end gap-2 rounded-xl border border-border bg-background px-3 py-2 shadow-[var(--shadow-float)] focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/15`}
      >
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          placeholder={
            isStreaming
              ? "正在分析…"
              : compact
                ? "追问原因、证据或下一步…"
                : "继续追问原因、证据或下一轮方案…"
          }
          disabled={disabled}
          rows={1}
          className="flex-1 bg-transparent resize-none outline-none text-sm placeholder:text-muted-foreground disabled:opacity-50 max-h-40"
        />
        {isStreaming && onStop ? (
          <button
            onClick={onStop}
            title="停止分析"
            aria-label="停止分析"
            className="flex-shrink-0 p-1.5 rounded-md text-muted-foreground hover:text-red-500 hover:bg-red-50 transition-colors"
          >
            <Square className="w-4 h-4" />
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={disabled || !text.trim()}
            aria-label="发送"
            className="flex-shrink-0 p-1.5 rounded-md text-primary hover:bg-primary/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <Send className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}
