import { lazy, Suspense, useEffect, useState } from "react";
import { useThemeStore } from "@/store/theme";
import { Settings } from "lucide-react";
import { listConversations, listEngines } from "@/api/conversations";
import {
  getDisplaySettings,
  getLlmSettings,
} from "@/api/settings";
import { useConversationsStore } from "@/store/conversations";
import { useDisplayStore } from "@/store/display";
import { Sidebar } from "@/components/Sidebar/Sidebar";
import { ChatView } from "@/components/Chat/ChatView";
import { AppLogo } from "@/components/Brand/AppLogo";

const SettingsModal = lazy(() =>
  import("@/components/Settings/SettingsModal").then((module) => ({
    default: module.SettingsModal,
  })),
);
const OnboardingWizard = lazy(() =>
  import("@/components/Onboarding/OnboardingWizard").then((module) => ({
    default: module.OnboardingWizard,
  })),
);

// Captured before any effects run — the hash effect clears it immediately,
// so the LLM-settings effect would see an empty hash otherwise.
const FORCE_SETUP = window.location.hash === "#setup";

export default function App() {
  const { conversations, activeId, setActiveId, setConversations, setEngines } =
    useConversationsStore();
  const { setDisplay } = useDisplayStore();
  const { theme } = useThemeStore();

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // Keep browser tab title in sync with the configured agent name
  const { appName } = useDisplayStore();
  useEffect(() => {
    document.title = appName || "Campaign Intelligence";
  }, [appName]);

  const [showSettings, setShowSettings] = useState(false);
  const [hasModelKey, setHasModelKey] = useState<boolean | null>(null);
  // null = still checking; true = show; false = don't show
  const [showOnboarding, setShowOnboarding] = useState<boolean | null>(
    FORCE_SETUP ? true : null,
  );

  // #setup hash — works on mount AND when typed into the address bar while app is open
  useEffect(() => {
    if (FORCE_SETUP) {
      history.replaceState(null, "", window.location.pathname);
      localStorage.removeItem("onboarding_dismissed");
    }

    const onHash = () => {
      if (window.location.hash === "#setup") {
        history.replaceState(null, "", window.location.pathname);
        localStorage.removeItem("onboarding_dismissed");
        setShowSettings(false);
        setShowOnboarding(true);
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    listConversations().then(setConversations).catch(console.error);
    listEngines().then(setEngines).catch(console.error);
    getDisplaySettings()
      .then((d) => setDisplay(d.app_name, d.logo_url))
      .catch(() => {});

    // Skip the has_key check when #setup forced the wizard open
    if (FORCE_SETUP) return;

    // Import and inspect data before model setup; don't hide the primary task.
    setShowOnboarding(false);
  }, [setConversations, setDisplay, setEngines]);

  useEffect(() => {
    if (!showSettings)
      getLlmSettings()
        .then((s) => setHasModelKey(s.has_key))
        .catch(() => setHasModelKey(null));
  }, [showSettings]);

  return (
    <div className="flex h-[100dvh] bg-background text-foreground overflow-hidden">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        <div
          className="flex min-h-12 items-center gap-3 border-b border-border bg-[hsl(var(--surface))] px-4"
          data-print-hide
        >
          <div className="mr-auto md:hidden">
            <AppLogo />
          </div>
          {conversations.length > 0 && (
            <select
              aria-label="切换决策记录"
              value={activeId ?? ""}
              onChange={(event) => setActiveId(event.target.value || null)}
              className="h-8 min-w-0 max-w-[138px] rounded-md border border-border bg-background px-2 text-xs text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary md:hidden"
            >
              <option value="">活动列表</option>
              {conversations.map((conversation) => (
                <option key={conversation.id} value={conversation.id}>
                  {conversation.title}
                </option>
              ))}
            </select>
          )}
          {hasModelKey === false && (
            <>
              <span
                className="h-2 w-2 shrink-0 rounded-full bg-amber-500 sm:hidden"
                title="DeepSeek 尚未连接"
              />
              <p className="mr-auto hidden min-w-0 flex-1 truncate text-xs text-muted-foreground sm:block">
                <span className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-amber-500 align-middle" />
                DeepSeek 未连接；数据核算可用，生成决策记录前需完成配置。
              </p>
            </>
          )}
          <button
            onClick={() => setShowSettings(true)}
            className="relative flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground
                       px-2.5 py-1.5 rounded-md hover:bg-muted/60 transition-colors"
            title="模型与设置"
          >
            <span className="relative">
              <Settings className="w-3.5 h-3.5" />
            </span>
            <span className="hidden sm:inline">模型与设置</span>
          </button>
        </div>
        <ChatView onOpenSettings={() => setShowSettings(true)} />
      </main>

      <Suspense fallback={null}>
        {showSettings && (
          <SettingsModal
            onClose={() => setShowSettings(false)}
            merchantMode
          />
        )}

        {showOnboarding === true && (
          <OnboardingWizard
            onComplete={() => {
              setShowOnboarding(false);
              setShowSettings(true); // land straight in Settings → Connections
            }}
            onDismiss={() => {
              localStorage.setItem("onboarding_dismissed", "1");
              setShowOnboarding(false);
            }}
          />
        )}
      </Suspense>
    </div>
  );
}
