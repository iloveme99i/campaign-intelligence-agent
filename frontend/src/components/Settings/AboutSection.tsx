import { useEffect, useState } from "react";
import { ExternalLink, GitBranch, ShieldCheck, Tag } from "lucide-react";
import { getVersionInfo, type VersionInfo } from "@/api/settings";

const UPSTREAM_URL = "https://github.com/datahub-project/analytics-agent";

export function AboutSection() {
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);

  useEffect(() => {
    getVersionInfo().then(setVersionInfo).catch(() => {});
  }, []);

  return (
    <div className="space-y-5">
      <section className="rounded-lg border border-border p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              Product build
            </p>
            <h3 className="mt-1 text-base font-semibold">Campaign Intelligence</h3>
            <p className="mt-1 max-w-md text-xs leading-5 text-muted-foreground">
              面向活动经营决策的证据约束分析 Agent。当前为本地构建，不与上游项目版本做自动比较。
            </p>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2.5 py-1 font-mono text-xs">
            <Tag className="h-3 w-3" />
            v{versionInfo?.current_version ?? "—"}
          </span>
        </div>
      </section>

      <section className="rounded-lg border border-border p-4">
        <div className="flex items-center gap-2">
          <GitBranch className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">代码来源与产品边界</h3>
        </div>
        <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-[8rem_1fr]">
          <dt className="text-muted-foreground">开源基座</dt>
          <dd>DataHub Analytics Agent</dd>
          <dt className="text-muted-foreground">许可证</dt>
          <dd>Apache License 2.0</dd>
          <dt className="text-muted-foreground">本产品新增</dt>
          <dd className="leading-5">
            商家数据契约、确定性指标核算、任务驱动诊断、证据编号、38 项质量门禁、人工决策、实验规划与执行结果回传。
          </dd>
        </dl>
        <a
          href={UPSTREAM_URL}
          target="_blank"
          rel="noreferrer"
          className="mt-4 inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
        >
          查看开源基座
          <ExternalLink className="h-3 w-3" />
        </a>
      </section>

      <section className="rounded-lg border border-border bg-muted/25 p-4">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-emerald-700" />
          <h3 className="text-sm font-semibold">当前能力声明</h3>
        </div>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">
          本地快照只读分析；合成案例用于功能与评测验证，不代表真实商家上线效果。观察性前后对比不被描述为因果增量。
        </p>
      </section>
    </div>
  );
}
