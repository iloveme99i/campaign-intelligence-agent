from analytics_agent.merchant.journey import analyze_path_bottlenecks


def test_identifies_lowest_adjacent_stage_without_claiming_recoverable_uplift():
    result = analyze_path_bottlenecks(
        [
            {
                "variant": "阶段任务",
                "exposed_users": 540,
                "landing_users": 431,
                "claim_users": 315,
                "activated_users": 237,
                "path_buyer_users": 46,
            }
        ]
    )

    assert result["status"] == "evaluated"
    assert result["rows"] == [
        {
            "variant": "阶段任务",
            "stage_key": "activation_to_path_purchase",
            "stage_label": "行动→完整购买",
            "from_users": 237,
            "to_users": 46,
            "continuation_rate": 46 / 237,
            "not_continued_users": 191,
        }
    ]
    assert "不等于可恢复增量" in result["interpretation"]
    assert "不证明原因" in result["interpretation"]


def test_rejects_non_monotonic_path_instead_of_manufacturing_a_bottleneck():
    result = analyze_path_bottlenecks(
        [
            {
                "variant": "A",
                "exposed_users": 100,
                "landing_users": 110,
                "claim_users": 80,
                "activated_users": 40,
                "path_buyer_users": 10,
            }
        ]
    )

    assert result["status"] == "invalid_sequence"
    assert result["rows"] == []
    assert result["invalid_variants"] == ["A"]


def test_zero_denominator_stage_is_skipped_and_ties_prefer_later_stage():
    result = analyze_path_bottlenecks(
        [
            {
                "variant": "A",
                "exposed_users": 100,
                "landing_users": 50,
                "claim_users": 25,
                "activated_users": 0,
                "path_buyer_users": 0,
            }
        ]
    )

    assert result["rows"][0]["stage_key"] == "claim_to_activation"
    assert result["rows"][0]["continuation_rate"] == 0
