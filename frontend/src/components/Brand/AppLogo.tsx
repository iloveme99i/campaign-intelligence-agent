import { useDisplayStore } from "@/store/display";

export function AppLogo() {
  const { appName, logoUrl } = useDisplayStore();

  return (
    <div className="flex items-center gap-2 select-none">
      {logoUrl ? (
        <img
          src={logoUrl}
          alt="品牌标识"
          className="h-7 w-7 flex-shrink-0 rounded-md object-contain"
          onError={(e) => ((e.target as HTMLImageElement).style.display = "none")}
        />
      ) : (
        <svg className="h-7 w-7 text-primary" viewBox="0 0 28 28" fill="none" aria-hidden>
          <rect x="1" y="1" width="26" height="26" rx="6" fill="currentColor" />
          <path d="M8.25 8.5h11.5M8.25 13.75h7.5M8.25 19h9.25" stroke="white" strokeWidth="1.65" strokeLinecap="round" />
          <circle cx="19.75" cy="13.75" r="1.25" fill="white" />
        </svg>
      )}
      <span
        className="whitespace-nowrap font-['Space_Grotesk_Variable'] text-[15px] font-semibold tracking-[-0.025em] text-foreground"
      >
        {appName || "Campaign Intelligence"}
      </span>
    </div>
  );
}
