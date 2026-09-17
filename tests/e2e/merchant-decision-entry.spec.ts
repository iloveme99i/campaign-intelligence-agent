import { expect, test } from "@playwright/test";

test.describe("Campaign Intelligence — decision entry", () => {
  test("loads a validated activity snapshot and requires a decision task", async ({ page }) => {
    await page.goto("/");

    await page.getByRole("button", { name: "打开跨业务合成案例" }).click();

    await expect(page.getByText("快照校验通过")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("heading", { name: "复盘结束后，你需要做什么决定？" })).toBeVisible();

    const start = page.getByRole("button", { name: "确认任务并开始核算" });
    await expect(start).toBeDisabled();

    await page.getByRole("radio", { name: /是否继续/ }).check();
    await expect(start).toBeEnabled();

    await expect(page.getByText(/系统自动核对：目标 · 路径 · 方案 · 成本/)).toBeVisible();
    await expect(page.getByText(/归因窗口/)).toBeVisible();
  });
});
