/** Stable labels for identifiers stored in sample data and historical decisions. */
export const VARIANT_NAMES: Record<string, string> = {
  control: "对照方案",
  discount_8: "8% 折扣方案",
  login_gift: "登录礼包",
  staged_missions: "阶段任务",
  single_discount: "单件直降",
  one_item_discount: "单件直降",
  live_bundle: "直播组合包",
  single_coupon: "单券承接",
  content_coupon: "内容与券联动",
};

export const BUSINESS_TERM_NAMES: Record<string, string> = {
  ...VARIANT_NAMES,
  dormant_30d: "沉默 30 天会员",
  observational_readout: "观察性对比",
  store_central: "中心经营点",
  store_north: "北区经营点",
  store_south: "南区经营点",
  mini_program: "小程序",
  official_account: "公众号",
  video_account: "视频号",
};

export function merchantName(value: string): string {
  return BUSINESS_TERM_NAMES[value] ?? value.replace(/_/g, " ");
}

export function variantName(value: string): string {
  return VARIANT_NAMES[value] ?? value.replace(/_/g, " ");
}
