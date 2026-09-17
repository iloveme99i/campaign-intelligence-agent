import { describe, expect, it } from "vitest";
import {
  displayEvidenceReferences,
  evidenceCoverage,
  parseDecisionSections,
} from "./DecisionRecord";

describe("parseDecisionSections", () => {
  it("parses numbered markdown headings", () => {
    const result = parseDecisionSections(
      "## 1. 结论一句话\n达标但更贵。\n## 2. 已确认事实\n- 目标达成。\n" +
        "## 3. 解释假设（最多三条）\n- 假设。\n## 4. 待验证项\n- 随机性。\n" +
        "## 5. 下一轮行动\n- 改变对象。",
    );

    expect(result.conclusion).toBe("达标但更贵。");
    expect(result.facts).toBe("- 目标达成。");
    expect(result.action).toBe("- 改变对象。");
  });

  it("parses bold provider headings and uses the preamble as conclusion", () => {
    const result = parseDecisionSections(
      "**总盘结论：目标达成，但贡献额下降。**\n\n" +
        "**已确认事实（活动 2026-08-08 至 2026-08-14）**\n- 目标达成。\n" +
        "**解释假设（最多三条）**\n1. 可能受折扣影响。\n" +
        "**待验证项**\n- 随机分流。\n**下一轮行动**\n- 改变对象。",
    );

    expect(result.conclusion).toContain("目标达成，但贡献额下降");
    expect(result.facts).toBe("- 目标达成。");
    expect(result.unknowns).toBe("- 随机分流。");
  });

  it("removes a redundant generated report title from the conclusion", () => {
    const result = parseDecisionSections(
      "## 夏日会员召回活动复盘（口径：8/1–8/7）\n\n" +
        "**总盘结论：目标达成，但贡献下降。**\n\n" +
        "### 一、已确认事实\n- 目标达成。\n" +
        "### 二、解释假设\n- 假设。\n### 三、待验证项\n- 缺口。\n" +
        "### 四、下一轮行动\n- 改变对象。",
    );

    expect(result.conclusion).toBe("**总盘结论：目标达成，但贡献下降。**");
  });

  it("parses a Chinese-numbered total conclusion heading", () => {
    const result = parseDecisionSections(
      "## 一、总盘结论\n目标达成，但贡献下降。\n" +
        "## 二、已确认事实\n- 目标达成。\n" +
        "## 三、解释假设\n- 假设。\n## 四、待验证项\n- 缺口。\n" +
        "## 五、下一轮行动\n- 改变对象。",
    );

    expect(result.conclusion).toBe("目标达成，但贡献下降。");
  });

  it("separates cost provenance and superseded conclusions from the headline", () => {
    const result = parseDecisionSections(
      "## 一、总盘结论\n目标未达成。\n" +
        "## 二、成本归属核对（重要）\n- 共享费用未分摊。\n" +
        "## 三、已确认事实\n- 转化率低于目标。\n" +
        "## 四、旧结论失效说明\n- 全渠道读数不适用。\n" +
        "## 五、解释假设\n- 可能有路径损耗。\n" +
        "## 六、待验证项\n- 分流质量。\n" +
        "## 七、下一轮行动\n- 改变入口。",
    );

    expect(result.conclusion).toBe("目标未达成。");
    expect(result.costBoundary).toBe("- 共享费用未分摊。");
    expect(result.facts).toBe("- 转化率低于目标。");
    expect(result.supersession).toBe("- 全渠道读数不适用。");
  });
});

describe("evidenceCoverage", () => {
  it("includes independently generated diagnostic evidence", () => {
    const base = {
      payload: {
        evidence_id: "q_12345678",
        related_evidence_ids: ["q_23456789"],
      },
    } as never;
    const diagnosis = {
      payload: {
        evidence_id: "q_34567890",
        related_evidence_ids: ["q_45678901"],
      },
    } as never;

    expect(
      evidenceCoverage("q_12345678 q_23456789 q_34567890 q_45678901", base, [
        diagnosis,
      ]),
    ).toEqual({ cited: 4, total: 4 });
  });
});

describe("displayEvidenceReferences", () => {
  it("keeps internal query ids out of the decision record", () => {
    const evidence = {
      payload: {
        evidence_manifest: [
          { kind: "outcome", evidence_id: "q_12345678" },
          { kind: "funnel", evidence_id: "q_23456789" },
        ],
      },
    } as never;

    expect(
      displayEvidenceReferences(
        "转化率已核对 q_12345678，路径损耗见 q_23456789。",
        evidence,
      ),
    ).toBe("转化率已核对 经营结果，路径损耗见 用户路径。");
  });

  it("renders stored variant codes as merchant-facing names", () => {
    expect(
      displayEvidenceReferences(
        "若 login_gift 低于 staged_missions，则停止 login_gift。",
      ),
    ).toBe("若 登录礼包 低于 阶段任务，则停止 登录礼包。");
  });

  it("renders codes from older saved decisions without changing the source answer", () => {
    expect(
      displayEvidenceReferences(
        "在 dormant_30d 人群内比较 discount_8 与 control；" +
          "one_item_discount 仅为 observational_readout，" +
          "experiment.status=observational_readout。",
      ),
    ).toBe(
      "在 沉默 30 天会员 人群内比较 8% 折扣方案 与 对照方案；" +
        "单件直降 仅为 观察性对比，实验状态为观察性对比。",
    );
  });

  it("labels diagnostic cost evidence by business provenance", () => {
    const diagnostic = {
      payload: {
        evidence_id: "q_12345678",
        evidence_manifest: [
          { kind: "diagnosis:variant:cost", evidence_id: "q_23456789" },
        ],
      },
    } as never;

    expect(
      displayEvidenceReferences(
        "方案路径 q_12345678；共享费用 q_23456789。",
        undefined,
        [diagnostic],
      ),
    ).toBe("方案路径 定向诊断；共享费用 共享费用明细。");
  });
});
