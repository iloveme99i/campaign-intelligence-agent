"""Deterministic adjacent-stage diagnosis for the campaign journey."""

from __future__ import annotations

from collections.abc import Sequence

STAGES = (
    ("exposure_to_landing", "曝光→访问", "exposed_users", "landing_users"),
    ("landing_to_claim", "访问→领取", "landing_users", "claim_users"),
    ("claim_to_activation", "领取→行动", "claim_users", "activated_users"),
    (
        "activation_to_path_purchase",
        "行动→完整购买",
        "activated_users",
        "path_buyer_users",
    ),
)


def analyze_path_bottlenecks(rows: Sequence[dict]) -> dict:
    """Find each variant's lowest adjacent-stage continuation rate.

    This is a descriptive location signal. It deliberately does not estimate
    recoverable users or claim why the loss happened.
    """
    diagnostics: list[dict] = []
    invalid_variants: list[str] = []
    for row in rows:
        variant = str(row.get("variant") or "未分组")
        candidates: list[dict] = []
        for index, (key, label, from_key, to_key) in enumerate(STAGES):
            before = row.get(from_key)
            after = row.get(to_key)
            if (
                isinstance(before, bool)
                or isinstance(after, bool)
                or not isinstance(before, int | float)
                or not isinstance(after, int | float)
                or before < 0
                or after < 0
                or after > before
            ):
                invalid_variants.append(variant)
                candidates = []
                break
            if before == 0:
                continue
            candidates.append(
                {
                    "stage_key": key,
                    "stage_label": label,
                    "from_users": int(before),
                    "to_users": int(after),
                    "continuation_rate": after / before,
                    "not_continued_users": int(before - after),
                    "stage_index": index,
                }
            )
        if candidates:
            # A tie resolves to the later stage because it is closer to the
            # business outcome and therefore the more actionable review point.
            bottleneck = min(
                candidates,
                key=lambda item: (item["continuation_rate"], -item["stage_index"]),
            )
            diagnostics.append(
                {
                    "variant": variant,
                    **{key: value for key, value in bottleneck.items() if key != "stage_index"},
                }
            )
    return {
        "status": "invalid_sequence" if invalid_variants else "evaluated",
        "basis": "lowest_adjacent_stage_continuation",
        "rows": diagnostics,
        "invalid_variants": list(dict.fromkeys(invalid_variants)),
        "interpretation": (
            "仅定位相邻阶段中的最低承接点；未进入下一步的用户不等于可恢复增量，也不证明原因。"
        ),
    }
