import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const baseUrl = process.env.MERCHANT_REVIEW_URL ?? "http://127.0.0.1:8101";
const outputDir = resolve(process.cwd(), "../.impeccable/review");

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});

async function openLatestExample(page) {
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  const snapshots = page.getByLabel("数据快照");
  const options = await snapshots.locator("option").evaluateAll((items) =>
    items.map((item) => ({ label: item.textContent ?? "", value: item.value })),
  );
  const latest = options.at(-1);
  if (!latest?.value) throw new Error("No campaign snapshot is available");
  await snapshots.selectOption(latest.value);
  await page.getByText("3 个活动", { exact: false }).waitFor();

  const bodyText = await page.locator("body").innerText();
  if (bodyText.includes("�")) throw new Error("Replacement character found in rendered UI");
  for (const title of ["腾讯零售 · 私域联动", "腾讯游戏 · 回流任务季", "字节系 · 全域大促"]) {
    if (!bodyText.includes(title)) throw new Error(`Missing scenario: ${title}`);
  }
}

for (const capture of [
  { name: "welcome-desktop", width: 1440, height: 900 },
  { name: "welcome-mobile", width: 375, height: 812 },
]) {
  const page = await browser.newPage({ viewport: capture });
  await openLatestExample(page);
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  if (width !== capture.width) throw new Error(`${capture.name} overflow: ${width}px`);
  await page.screenshot({
    path: resolve(outputDir, `${capture.name}.png`),
    fullPage: false,
  });

  await page.getByRole("button", { name: /导入活动数据/ }).click();
  await page.getByRole("button", { name: "校验并导入" }).waitFor();
  await page.screenshot({
    path: resolve(outputDir, `${capture.name}-import-open.png`),
    fullPage: false,
  });
  await page.close();
}

await browser.close();
console.log(`Captured hardened welcome states in ${outputDir}`);
