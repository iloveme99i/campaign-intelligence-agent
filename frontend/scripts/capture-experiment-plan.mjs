import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const baseUrl = process.env.MERCHANT_REVIEW_URL ?? "http://127.0.0.1:8101";
const outputDir = resolve(process.cwd(), "../.impeccable/review");
const conversationId = "73124a25-1763-4959-9464-f41e828d95a8";
const conversationTitle = "腾讯游戏 · 回流任务季｜继续判断";
const otherConversationId = "7fb676ad-9d2a-4765-93f4-1217b077d074";
const otherConversationTitle = "字节系 · 全域大促｜扩量判断";

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath:
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});

for (const capture of [
  { name: "experiment-plan-desktop", width: 1440, height: 900 },
  { name: "experiment-plan-mobile", width: 375, height: 812 },
]) {
  const errors = [];
  const page = await browser.newPage({ viewport: capture });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  if (capture.width < 768) {
    await page.getByLabel("切换决策记录").selectOption(conversationId);
    await page.getByRole("button", { name: "决策记录" }).click();
  } else {
    await page.getByTitle(conversationTitle, { exact: true }).click();
  }

  const section = page.getByRole("heading", { name: "下一轮实验设计" });
  await section.waitFor();
  await page.getByText("8,802 人", { exact: true }).waitFor();
  await page.getByRole("button", { name: "确认并测算" }).click();
  await page
    .getByText("规划参数已确认，将随本轮决策落档", { exact: true })
    .waitFor();
  await section.scrollIntoViewIfNeeded();
  const decisionPane = page.getByRole("complementary", { name: "决策记录" });
  const paneText = await decisionPane.innerText();
  if (/q_[0-9a-f]{8,}/.test(paneText)) {
    throw new Error(`${capture.name}: internal evidence id is visible`);
  }
  if (paneText.includes("�")) {
    throw new Error(`${capture.name}: replacement character found`);
  }
  if (errors.length) throw new Error(`${capture.name}: ${errors.join("; ")}`);
  const scrollWidth = await page.evaluate(
    () => document.documentElement.scrollWidth,
  );
  if (scrollWidth !== capture.width) {
    throw new Error(`${capture.name}: horizontal overflow ${scrollWidth}px`);
  }

  await page.screenshot({
    path: resolve(outputDir, `${capture.name}.png`),
    fullPage: false,
  });

  if (capture.width < 768) {
    await page.getByLabel("切换决策记录").selectOption(otherConversationId);
    await page.getByLabel("切换决策记录").selectOption(conversationId);
  } else {
    await page.getByTitle(otherConversationTitle, { exact: true }).click();
    await page.getByTitle(conversationTitle, { exact: true }).click();
  }
  await page.getByRole("button", { name: "确认并测算" }).waitFor();
  if (
    await page
      .getByText("规划参数已确认，将随本轮决策落档", { exact: true })
      .isVisible()
      .catch(() => false)
  ) {
    throw new Error(`${capture.name}: experiment state leaked across records`);
  }
  await page.close();
}

await browser.close();
console.log(`Captured verified experiment plan states in ${outputDir}`);
