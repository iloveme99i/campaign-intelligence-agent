import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const baseUrl = process.env.MERCHANT_REVIEW_URL ?? "http://127.0.0.1:8101";
const outputDir = resolve(process.cwd(), "../.impeccable/review");
const conversationTitle =
  process.env.CAMPAIGN_CONVERSATION_TITLE ??
  "决策级因果边界验收";

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});

async function openReview(page) {
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  const mobileSwitcher = page.getByLabel("切换决策记录");
  if (await mobileSwitcher.isVisible()) {
    await mobileSwitcher.selectOption({ label: conversationTitle });
  } else {
    await page.getByText(conversationTitle, { exact: true }).first().click();
  }
  await page.getByRole("heading", { name: /主指标.*贡献额/ }).waitFor();
}

for (const capture of [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  const page = await browser.newPage({ viewport: capture });
  await openReview(page);
  if (capture.name === "mobile") {
    const width = await page.evaluate(
      () => document.documentElement.scrollWidth,
    );
    if (width !== capture.width) throw new Error(`mobile overflow: ${width}px`);
  }
  await page.screenshot({
    path: resolve(outputDir, `${capture.name}.png`),
    fullPage: false,
  });
  await page.close();
}

const mobileDecisionPage = await browser.newPage({
  viewport: { width: 375, height: 812 },
});
await openReview(mobileDecisionPage);
await mobileDecisionPage.getByRole("button", { name: "决策记录" }).click();
await mobileDecisionPage.getByText("调查路径", { exact: true }).waitFor();
await mobileDecisionPage.screenshot({
  path: resolve(outputDir, "mobile-decision.png"),
  fullPage: false,
});
await mobileDecisionPage.close();

const evidencePage = await browser.newPage({
  viewport: { width: 1440, height: 900 },
});
await openReview(evidencePage);
await evidencePage.getByText("证据与复盘口径", { exact: true }).click();
await evidencePage.screenshot({
  path: resolve(outputDir, "evidence-appendix.png"),
  fullPage: true,
});
await evidencePage.close();

await browser.close();
console.log(`Captured desktop and mobile review states in ${outputDir}`);
