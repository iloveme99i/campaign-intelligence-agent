import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DataReliability } from "./DataReliability";

const quality = {
  exposed_users: 100,
  buyer_users: 10,
  cross_variant_exposed_users: 0,
  buyers_without_prior_exposure: 0,
};

describe("DataReliability coverage disclosure", () => {
  it("separates global path linkage from mismatched channel labels", () => {
    const html = renderToStaticMarkup(<DataReliability quality={{
      ...quality,
      buyers_without_matching_variant_exposure: 0,
      buyers_without_matching_channel_exposure: 1,
      buyers_without_matching_location_exposure: 0,
      buyers_without_matching_audience_exposure: 0,
    }} />);
    expect(html).toContain("10 / 10");
    expect(html).toContain("渠道 1 人");
    expect(html).toContain("购买/曝光率及实验测算已暂停");
  });

  it("warns on a day without observed rows without calling it a missing export", () => {
    const html = renderToStaticMarkup(<DataReliability quality={quality} coverage={{
      baseline_days: 7, activity_days: 7, baseline_days_with_orders: 7,
      activity_days_with_orders: 6, activity_days_with_exposure: 7,
      last_observed_activity_order_date: "2026-08-12",
    }} />);
    expect(html).toContain("6/7 天");
    expect(html).toContain("可能是真实零单/零曝光，也可能是导出漏行");
    expect(html).toContain("这不是数据导出截止时间");
  });

  it("does not equate rows on every day with complete exports", () => {
    const html = renderToStaticMarkup(<DataReliability quality={quality} coverage={{
      baseline_days: 7, activity_days: 7, baseline_days_with_orders: 7,
      activity_days_with_orders: 7, activity_days_with_exposure: 7,
      last_observed_activity_order_date: null,
    }} />);
    expect(html).toContain("仍不能证明导出完整");
  });
});
