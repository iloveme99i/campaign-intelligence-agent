"""Deterministic, cross-industry synthetic campaigns for the review journey.

The scenarios borrow only public campaign mechanics. Every row is generated here;
none of the figures represents ByteDance, Tencent, a merchant, or a player account.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from analytics_agent.merchant.importer import TABLE_FIELDS


@dataclass(frozen=True)
class Scenario:
    campaign_id: str
    campaign_name: str
    objective: str
    start: date
    primary_metric: str
    target_value: str
    attribution_days: int
    variants: tuple[str, str]
    channels: tuple[str, ...]
    locations: tuple[str, ...]
    audiences: tuple[str, ...]
    exposed_users: int
    seed: int
    # Per variant: landing, claim, activation and purchase probability in per-mille.
    rates: tuple[tuple[int, int, int, int], tuple[int, int, int, int]]
    gross_cents: tuple[int, int]
    merchant_discount_cents: tuple[int, int]
    platform_discount_cents: tuple[int, int]
    unit_cost_cents: tuple[int, int]
    daily_media_cents: tuple[int, int]
    baseline_orders: int
    baseline_gross_cents: int
    baseline_unit_cost_cents: int


SCENARIOS = (
    Scenario(
        campaign_id="byte_full_funnel_sale",
        campaign_name="字节系 · 全域大促",
        objective="参考抖音电商公开大促的短视频、直播、商城和优惠叠加机制；判断哪种承接方案值得扩大。数据完全合成。",
        start=date(2026, 6, 12),
        primary_metric="roi",
        target_value="1.35",
        attribution_days=3,
        variants=("单件直降", "直播组合包"),
        channels=("short_video", "live_room", "mall"),
        locations=("online_flagship", "regional_store"),
        audiences=("new_customer", "returning_customer"),
        exposed_users=960,
        seed=17,
        rates=((820, 710, 690, 220), (770, 650, 760, 255)),
        gross_cents=(8_900, 11_900),
        merchant_discount_cents=(1_800, 1_200),
        platform_discount_cents=(500, 700),
        unit_cost_cents=(4_300, 5_500),
        daily_media_cents=(13_500, 16_000),
        baseline_orders=112,
        baseline_gross_cents=9_300,
        baseline_unit_cost_cents=4_700,
    ),
    Scenario(
        campaign_id="tencent_game_return_ops",
        campaign_name="腾讯游戏 · 回流任务季",
        objective="参考腾讯游戏公开活动的回流分层、日/周/累计任务和阶段奖励机制；判断任务链是否应继续。数据完全合成。",
        start=date(2026, 7, 16),
        primary_metric="conversion_rate",
        target_value="0.075",
        attribution_days=7,
        variants=("登录礼包", "阶段任务"),
        channels=("game_client", "wechat_game", "wegame"),
        locations=("server_east", "server_south", "server_west"),
        audiences=("returning_30d", "returning_60d"),
        exposed_users=1_080,
        seed=31,
        rates=((860, 820, 500, 125), (790, 720, 770, 155)),
        gross_cents=(4_800, 5_600),
        merchant_discount_cents=(300, 450),
        platform_discount_cents=(0, 0),
        unit_cost_cents=(1_300, 1_550),
        daily_media_cents=(10_000, 13_800),
        baseline_orders=73,
        baseline_gross_cents=4_900,
        baseline_unit_cost_cents=1_350,
    ),
    Scenario(
        campaign_id="tencent_private_retail",
        campaign_name="腾讯零售 · 私域联动",
        objective="参考腾讯智慧零售公开案例中的内容触达、小程序承接、发券与支付链路；判断内容与券联动是否保留。数据完全合成。",
        start=date(2026, 8, 6),
        primary_metric="net_revenue",
        target_value="620000",
        attribution_days=3,
        variants=("单券承接", "内容与券联动"),
        channels=("official_account", "mini_program", "video_account"),
        locations=("store_north", "store_central", "store_south"),
        audiences=("member_active", "member_dormant"),
        exposed_users=900,
        seed=47,
        rates=((800, 690, 640, 205), (740, 610, 790, 245)),
        gross_cents=(7_200, 8_100),
        merchant_discount_cents=(1_000, 800),
        platform_discount_cents=(400, 400),
        unit_cost_cents=(3_100, 3_500),
        daily_media_cents=(7_200, 11_200),
        baseline_orders=94,
        baseline_gross_cents=7_400,
        baseline_unit_cost_cents=3_200,
    ),
)


def _draw(index: int, seed: int, stage: int) -> int:
    """Return a stable pseudo-random integer in [0, 1000)."""
    digest = hashlib.blake2b(f"{seed}:{index}:{stage}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % 1000


def _staged_timestamp(stamp: str, minutes: int) -> str:
    return (datetime.fromisoformat(stamp) + timedelta(minutes=minutes)).isoformat()


def _append_scenario(rows: dict[str, list[list]], scenario: Scenario) -> None:
    end = scenario.start + timedelta(days=6)
    rows["campaigns"].append(
        [
            scenario.campaign_id,
            scenario.campaign_name,
            scenario.objective,
            scenario.start.isoformat(),
            end.isoformat(),
            scenario.primary_metric,
            scenario.target_value,
            scenario.attribution_days,
        ]
    )

    # Matched prior-period business outcomes are contextual, not attributed conversions.
    for index in range(scenario.baseline_orders):
        day = index % 7
        stamp = scenario.start - timedelta(days=7 - day)
        variant_index = index % 2
        rows["orders"].append(
            [
                f"{scenario.campaign_id}_base_o_{index:04d}",
                f"{scenario.campaign_id}_base_u_{index:04d}",
                f"{stamp.isoformat()}T{9 + index % 11:02d}:{index % 60:02d}:00",
                "",
                scenario.variants[variant_index],
                scenario.channels[index % len(scenario.channels)],
                scenario.locations[index % len(scenario.locations)],
                scenario.audiences[index % len(scenario.audiences)],
                "completed",
                scenario.baseline_gross_cents,
                0,
                0,
                500 if index % 37 == 0 else 0,
                scenario.baseline_unit_cost_cents,
            ]
        )

    event_index = 0
    for index in range(scenario.exposed_users):
        variant_index = index % 2
        variant = scenario.variants[variant_index]
        channel = scenario.channels[(index // 2) % len(scenario.channels)]
        location = scenario.locations[(index // 6) % len(scenario.locations)]
        audience = scenario.audiences[(index // 3) % len(scenario.audiences)]
        day = index % 7
        stamp = f"{(scenario.start + timedelta(days=day)).isoformat()}T{9 + index % 11:02d}:{index % 60:02d}:00"
        user = f"{scenario.campaign_id}_u_{index:04d}"
        rates = scenario.rates[variant_index]

        def add_event(
            name: str,
            event_user: str,
            event_stamp: str,
            event_variant: str,
            event_channel: str,
            event_location: str,
            event_audience: str,
        ) -> None:
            nonlocal event_index
            rows["events"].append(
                [
                    f"{scenario.campaign_id}_e_{event_index:05d}",
                    event_user,
                    event_stamp,
                    name,
                    scenario.campaign_id,
                    event_variant,
                    event_channel,
                    event_location,
                    event_audience,
                ]
            )
            event_index += 1

        event_dimensions = (variant, channel, location, audience)
        add_event("exposure", user, _staged_timestamp(stamp, 0), *event_dimensions)
        landed = _draw(index, scenario.seed, 1) < rates[0]
        claimed = landed and _draw(index, scenario.seed, 2) < rates[1]
        activated = claimed and _draw(index, scenario.seed, 3) < rates[2]
        converted = activated and _draw(index, scenario.seed, 4) < rates[3]
        if landed:
            add_event("landing_view", user, _staged_timestamp(stamp, 1), *event_dimensions)
        if claimed:
            add_event("offer_claim", user, _staged_timestamp(stamp, 2), *event_dimensions)
        if activated:
            add_event("activation", user, _staged_timestamp(stamp, 3), *event_dimensions)
        if converted:
            refund = 900 if (index + scenario.seed) % 53 == 0 else 0
            rows["orders"].append(
                [
                    f"{scenario.campaign_id}_o_{index:04d}",
                    user,
                    _staged_timestamp(stamp, 4),
                    scenario.campaign_id,
                    variant,
                    channel,
                    location,
                    audience,
                    "completed",
                    scenario.gross_cents[variant_index],
                    scenario.merchant_discount_cents[variant_index],
                    scenario.platform_discount_cents[variant_index],
                    refund,
                    scenario.unit_cost_cents[variant_index],
                ]
            )

    for day in range(7):
        business_date = (scenario.start + timedelta(days=day)).isoformat()
        for variant_index, variant in enumerate(scenario.variants):
            rows["costs"].append(
                [
                    f"{scenario.campaign_id}_c_{variant_index}_{day}",
                    scenario.campaign_id,
                    business_date,
                    variant,
                    "all",
                    "media_and_reward",
                    scenario.daily_media_cents[variant_index],
                ]
            )

    # Synthetic, balanced business-unit panel for an auditable DiD walkthrough.
    # The pre-period treatment/control gap is stable by construction; treated
    # units receive heterogeneous lift only after the campaign starts.
    periods = [
        scenario.start - timedelta(days=21),
        scenario.start - timedelta(days=14),
        scenario.start - timedelta(days=7),
        scenario.start,
    ]
    panel_metrics = {
        "completed_orders": (82, 3, 4, 6, 10, 1),
        "buyer_users": (74, 3, 3, 5, 9, 1),
        "net_revenue_cents": (520_000, 20_000, 15_000, 30_000, 85_000, 5_000),
        "contribution_cents": (260_000, 12_000, 8_000, 20_000, 55_000, 4_000),
    }
    for metric, (
        base,
        unit_step,
        trend_step,
        group_gap,
        lift,
        lift_step,
    ) in panel_metrics.items():
        for treated in (0, 1):
            for unit_index in range(8):
                unit = f"{scenario.campaign_id}_{'t' if treated else 'c'}_{unit_index + 1:02d}"
                for period_index, period in enumerate(periods):
                    post = int(period_index == len(periods) - 1)
                    outcome = (
                        base
                        + unit_index * unit_step
                        + period_index * trend_step
                        + (group_gap if treated else 0)
                        + (lift + (unit_index % 4) * lift_step if treated and post else 0)
                    )
                    rows["incrementality"].append(
                        [
                            unit,
                            scenario.campaign_id,
                            period.isoformat(),
                            metric,
                            treated,
                            post,
                            outcome,
                            1,
                        ]
                    )


def example_exports() -> dict[str, bytes]:
    rows: dict[str, list[list]] = {name: [] for name in TABLE_FIELDS}
    for scenario in SCENARIOS:
        _append_scenario(rows, scenario)

    files = {}
    for name, fields in TABLE_FIELDS.items():
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(fields)
        writer.writerows(rows[name])
        files[name] = buffer.getvalue().encode("utf-8")
    return files


def example_archive(*, templates_only: bool = False) -> bytes:
    files = (
        {name: (",".join(fields) + "\n").encode() for name, fields in TABLE_FIELDS.items()}
        if templates_only
        else example_exports()
    )
    readme = """Campaign Intelligence · 跨业务合成示例 / 导入模板

数据完全合成，不含真实用户、玩家、商家或平台经营数据，不可用于声明字节跳动、腾讯或任何业务的真实收益。
三个活动只参考公开可见的活动机制：
1. 字节系 · 全域大促：短视频、直播、商城承接与优惠叠加；
2. 腾讯游戏 · 回流任务季：回流分层、日/周/累计任务与阶段奖励；
3. 腾讯零售 · 私域联动：内容触达、小程序、发券与支付链路。

每个活动包含两个方案组，用于验证目标、标准化路径、方案差异和成本价值。示例有意设置不同的业务张力，不能只看转化率下结论。
incrementality 是合成的同期经营单元平衡面板：8 个处理单元、8 个对照单元、3 个活动前周期和 1 个活动后周期，覆盖完成订单、购买用户、净收入和贡献额，用于演示 DiD、前趋势和安慰剂检验。它不是平台真实增量。

events 使用四级标准化旅程：exposure=获得触达，landing_view=进入承接页，offer_claim=领取权益或接受任务，activation=完成关键行为。不同业务的原始事件应在导出前映射到这四级口径。
user_key 必须是脱敏标识，不能填写姓名、手机号或设备原始标识。
金额均为整数分。订单净收入不扣平台承担优惠；退款不能超过顾客实付。
活动前后对比是相关性证据；随机实验或满足同期对照、平行趋势、稳定构成和无干扰假设的 DiD，才可讨论条件性因果增量。

解压后在首页选择四份必需 CSV；如需增量识别，再选择 incrementality.csv。模板文件只有表头，需填写后导入。
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in {
            **{f"{name}.csv": data for name, data in files.items()},
            "README.txt": readme.encode(),
        }.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 8, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return buffer.getvalue()
