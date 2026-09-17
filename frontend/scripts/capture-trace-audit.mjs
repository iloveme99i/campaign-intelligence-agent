import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const baseUrl = process.env.MERCHANT_REVIEW_URL ?? "http://127.0.0.1:8101";
const outputDir = resolve(process.cwd(), "../.impeccable/review");
const expectedTitle = "腾讯游戏 · 回流任务季｜继续判断";
const conversationId = "73124a25-1763-4959-9464-f41e828d95a8";

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});

for (const capture of [
  { name: "trace-audit-desktop", width: 1440, height: 900 },
  { name: "trace-audit-mobile", width: 375, height: 812 },
]) {
  const pageErrors = [];
  const page = await browser.newPage({ viewport: capture });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  if (capture.width < 768) {
    await page.getByLabel("切换决策记录").selectOption(conversationId);
  } else {
    await page.getByTitle(expectedTitle, { exact: true }).click();
  }

  if (capture.width < 768) {
    await page.getByRole("button", { name: "决策记录" }).click();
  }

  const traceAudit = page.locator("details").filter({ hasText: "运行审计" });
  await traceAudit.waitFor();
  await traceAudit.locator("summary").click();
  for (const label of ["口径完整", "证据闭环", "诊断克制", "决策边界"]) {
    await page.getByText(label, { exact: true }).waitFor();
  }

  const bodyText = await page.locator("body").innerText();
  if (bodyText.includes("�")) throw new Error(`${capture.name}: replacement character found`);
  if (pageErrors.length) throw new Error(`${capture.name}: ${pageErrors.join("; ")}`);
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  if (scrollWidth !== capture.width) {
    throw new Error(`${capture.name}: horizontal overflow ${scrollWidth}px`);
  }

  await page.screenshot({
    path: resolve(outputDir, `${capture.name}.png`),
    fullPage: false,
  });
  await page.close();
}

await browser.close();
console.log(`Captured verified trace audit states in ${outputDir}`);
