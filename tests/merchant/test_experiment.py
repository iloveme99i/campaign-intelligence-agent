import pytest
from analytics_agent.merchant.experiment import (
    assess_binary_outcome,
    assess_incrementality_readiness,
    assess_variants,
    plan_binary_experiment,
)


def test_incrementality_readiness_rejects_equal_window_shortcut():
    result = assess_incrementality_readiness(
        has_pre_assignment_eligibility=False,
        has_assignment_log=False,
        has_concurrent_control=False,
        randomization_unit=None,
        repeated_pre_periods=1,
        panel_unit_consistent=False,
        outcome_complete=True,
        interference_reviewed=False,
    )

    assert result["status"] == "not_identified"
    assert result["recommended_method"] is None
    assert "assignment_log" in result["randomized_experiment"]["missing"]
    assert "repeated_pre_periods" in result["difference_in_differences"]["missing"]
    assert any("等长前后窗口" in item for item in result["prohibited_shortcuts"])


def test_incrementality_readiness_prefers_randomized_itt():
    result = assess_incrementality_readiness(
        has_pre_assignment_eligibility=True,
        has_assignment_log=True,
        has_concurrent_control=True,
        randomization_unit="user_key",
        repeated_pre_periods=0,
        panel_unit_consistent=False,
        outcome_complete=True,
        interference_reviewed=True,
    )

    assert result["status"] == "randomized_ready"
    assert result["recommended_method"] == "randomized_intention_to_treat"
    assert result["randomized_experiment"]["missing"] == []


def test_incrementality_readiness_allows_did_only_with_panel_pretrends():
    result = assess_incrementality_readiness(
        has_pre_assignment_eligibility=False,
        has_assignment_log=False,
        has_concurrent_control=True,
        randomization_unit=None,
        repeated_pre_periods=3,
        panel_unit_consistent=True,
        outcome_complete=True,
        interference_reviewed=True,
    )

    assert result["status"] == "did_ready"
    assert result["difference_in_differences"]["ready"] is True
    assert "parallel_trends" in result["difference_in_differences"]["required_validation"]


def test_intersection_population_is_recomputed_from_snapshot(monkeypatch, tmp_path):
    from datetime import timedelta
    from pathlib import Path

    import orjson
    from analytics_agent.api.merchant import _build_experiment_plan
    from analytics_agent.merchant.engine import MerchantQueryEngine
    from analytics_agent.merchant.examples import SCENARIOS, example_exports
    from analytics_agent.merchant.importer import import_snapshot
    from analytics_agent.merchant.readonly import SnapshotReader
    from analytics_agent.merchant.scope import ReviewScope, funnel_query

    snapshot = import_snapshot(example_exports(), tmp_path)
    monkeypatch.setattr(
        "analytics_agent.merchant.storage.snapshot_path",
        lambda snapshot_id: tmp_path / f"{snapshot_id}.db",
    )
    scenario = SCENARIOS[-1]
    scope = ReviewScope.model_validate(
        {
            "campaign_id": scenario.campaign_id,
            "baseline": {
                "start": (scenario.start - timedelta(days=7)).isoformat(),
                "end": (scenario.start - timedelta(days=1)).isoformat(),
            },
            "activity": {
                "start": scenario.start.isoformat(),
                "end": (scenario.start + timedelta(days=6)).isoformat(),
            },
        }
    )
    evidence = orjson.loads(
        MerchantQueryEngine(Path(snapshot["path"]))
        .comparison_tool()
        .invoke(
            {
                "scope": scope.model_dump(mode="json"),
            }
        )
    )
    location = evidence["segments"]["location_id"][0]["dimension_value"]
    channel = evidence["segments"]["channel"][0]["dimension_value"]
    selected_scope = scope.model_copy(update={"location_id": location, "channel": channel})
    sql, parameters = funnel_query(selected_scope)
    expected = SnapshotReader(Path(snapshot["path"])).query(sql, parameters)["rows"]
    plan = _build_experiment_plan(
        evidence,
        mde_pp=5,
        traffic_share=1,
        planning_dimension="location_id",
        planning_value=location,
        secondary_dimension="channel",
        secondary_value=channel,
    )
    assert plan["population"]["eligible_users"] == sum(row["exposed_users"] for row in expected)
    assert plan["population"]["successful_users"] == sum(row["buyer_users"] for row in expected)
    assert plan["population"]["scope_id"] == selected_scope.scope_id
    assert plan["population"]["snapshot_id"] == snapshot["snapshot_id"]
    assert plan["population"]["source"] == "recomputed_snapshot_intersection"
    assert plan["population"]["eligible_users"] < next(
        row["exposed_users"]
        for row in evidence["segments"]["location_id"]
        if row["dimension_value"] == location
    )
    with pytest.raises(ValueError, match="two distinct dimensions"):
        _build_experiment_plan(
            evidence,
            mde_pp=5,
            traffic_share=1,
            planning_dimension="channel",
            planning_value=channel,
            secondary_dimension="channel",
            secondary_value=channel,
        )


def test_segment_plan_rejects_order_label_without_matching_exposure(monkeypatch, tmp_path):
    from pathlib import Path

    import orjson
    from analytics_agent.api.merchant import _build_experiment_plan
    from analytics_agent.merchant.engine import MerchantQueryEngine
    from analytics_agent.merchant.importer import import_snapshot
    from test_importer import exports

    files = exports()
    files["events"] += b"e4,u3,2026-08-01T11:03:00,exposure,c,A,push,store_1,dormant\n"
    files["orders"] = files["orders"].replace(
        b"b,u2,2026-08-01T11:10:00,c,B,sms,store_2,dormant,completed",
        b"b,u2,2026-08-01T11:10:00,c,B,push,store_2,dormant,completed",
    )
    snapshot = import_snapshot(files, tmp_path)
    monkeypatch.setattr(
        "analytics_agent.merchant.storage.snapshot_path",
        lambda snapshot_id: tmp_path / f"{snapshot_id}.db",
    )
    evidence = orjson.loads(
        MerchantQueryEngine(Path(snapshot["path"]))
        .comparison_tool()
        .invoke(
            {
                "scope": {
                    "campaign_id": "c",
                    "baseline": {"start": "2026-07-31", "end": "2026-07-31"},
                    "activity": {"start": "2026-08-01", "end": "2026-08-01"},
                },
            }
        )
    )
    assert evidence["data_quality"]["buyers_without_prior_exposure"] == 0
    assert evidence["data_quality"]["buyers_without_matching_channel_exposure"] == 1
    selected = next(
        row for row in evidence["segments"]["channel"] if row["dimension_value"] == "push"
    )
    assert selected["exposed_users"] == selected["buyer_users"] == 2
    with pytest.raises(ValueError, match="match prior exposure labels"):
        _build_experiment_plan(
            evidence,
            mde_pp=1,
            traffic_share=1,
            planning_dimension="channel",
            planning_value="push",
        )


def test_planning_population_matches_selected_stage_and_store():
    from analytics_agent.api.merchant import _build_experiment_plan

    evidence = {
        "scope": {"activity": {"start": "2026-08-06", "end": "2026-08-12"}},
        "funnel": [
            {
                "exposed_users": 900,
                "buyer_users": 63,
                "claim_users": 429,
                "activated_users": 285,
                "path_buyer_users": 63,
            }
        ],
        "segments": {
            "location_id": [
                {
                    "dimension_value": "store_central",
                    "total_groups": 2,
                    "exposed_users": 300,
                    "buyer_users": 26,
                    "claim_users": 141,
                    "activated_users": 96,
                    "path_buyer_users": 26,
                },
                {
                    "dimension_value": "store_south",
                    "total_groups": 2,
                    "exposed_users": 300,
                    "buyer_users": 17,
                    "claim_users": 141,
                    "activated_users": 83,
                    "path_buyer_users": 17,
                },
            ]
        },
        "data_quality": {"cross_variant_exposed_users": 0, "buyers_without_prior_exposure": 0},
    }
    plan = _build_experiment_plan(
        evidence,
        mde_pp=5,
        traffic_share=1,
        planning_dimension="location_id",
        planning_value="store_south",
        planning_metric="activation_per_claim",
    )
    assert plan["population"] == {
        "dimension": "location_id",
        "value": "store_south",
        "metric": "activation_per_claim",
        "eligible_users": 141,
        "successful_users": 83,
    }
    assert plan["baseline_rate"] == 83 / 141
    assert plan["observed_daily_eligible_users"] == 141 / 7
    assert plan["estimated_days"] > 7

    path_plan = _build_experiment_plan(
        evidence,
        mde_pp=5,
        traffic_share=1,
        planning_dimension="location_id",
        planning_value="store_south",
        planning_metric="path_purchase_per_activation",
    )
    assert path_plan["population"] == {
        "dimension": "location_id",
        "value": "store_south",
        "metric": "path_purchase_per_activation",
        "eligible_users": 83,
        "successful_users": 17,
    }
    assert path_plan["baseline_rate"] == 17 / 83


def test_planning_rejects_missing_or_truncated_segment():
    import pytest
    from analytics_agent.api.merchant import _build_experiment_plan

    evidence = {
        "scope": {"activity": {"start": "2026-08-06", "end": "2026-08-12"}},
        "funnel": [{"exposed_users": 900, "buyer_users": 63}],
        "segments": {
            "location_id": [
                {
                    "dimension_value": "store_south",
                    "total_groups": 3,
                    "exposed_users": 300,
                    "buyer_users": 17,
                }
            ]
        },
    }
    for value in ("store_south", "missing"):
        with pytest.raises(ValueError):
            _build_experiment_plan(
                evidence,
                mde_pp=1,
                traffic_share=1,
                planning_dimension="location_id",
                planning_value=value,
            )


def test_assesses_conversion_difference_without_causal_claim():
    result = assess_variants(
        [
            {"variant": "control", "exposed_users": 400, "buyer_users": 40},
            {"variant": "discount_8", "exposed_users": 400, "buyer_users": 58},
        ]
    )
    comparison = result["comparisons"][0]
    assert result["status"] == "observational_readout"
    assert comparison["difference_pp"] == pytest.approx(4.5)
    assert comparison["ci95_difference_pp"][0] < 0 < comparison["ci95_difference_pp"][1]
    assert comparison["p_value"] > 0.05
    assert comparison["descriptive_signal"] is False
    assert "无法验证随机分流" in result["assumption"]


def test_rejects_single_group_readout():
    result = assess_variants([{"variant": "control", "exposed_users": 12, "buyer_users": 2}])
    assert result["status"] == "insufficient_groups"
    assert result["comparisons"] == []


def test_blocks_statistics_when_buyers_cannot_link_prior_exposure():
    result = assess_variants(
        [
            {"variant": "control", "exposed_users": 100, "buyer_users": 10},
            {"variant": "treatment", "exposed_users": 100, "buyer_users": 12},
        ],
        buyers_without_prior_exposure=1,
    )
    assert result["status"] == "unlinked_buyers"
    assert result["comparisons"] == []


def test_blocks_variant_statistics_when_order_variant_differs_from_exposure():
    result = assess_variants(
        [
            {"variant": "control", "exposed_users": 100, "buyer_users": 10},
            {"variant": "treatment", "exposed_users": 100, "buyer_users": 12},
        ],
        buyers_without_matching_variant_exposure=1,
    )
    assert result["status"] == "dimension_mismatch"
    assert result["comparisons"] == []


def test_blocks_statistics_for_cross_variant_exposure():
    result = assess_variants(
        [
            {"variant": "control", "exposed_users": 100, "buyer_users": 10},
            {"variant": "treatment", "exposed_users": 100, "buyer_users": 12},
        ],
        cross_variant_exposed_users=2,
    )
    assert result["status"] == "contaminated_groups"
    assert result["comparisons"] == []


def test_flags_small_expected_cells():
    result = assess_variants(
        [
            {"variant": "control", "exposed_users": 10, "buyer_users": 0},
            {"variant": "treatment", "exposed_users": 10, "buyer_users": 3},
        ]
    )
    comparison = result["comparisons"][0]
    assert comparison["sample_check"] == "small_expected_cell"
    assert comparison["descriptive_signal"] is False


def test_plans_fixed_horizon_experiment_from_observed_eligible_traffic():
    plan = plan_binary_experiment(
        [
            {"variant": "control", "exposed_users": 500, "buyer_users": 25},
            {"variant": "treatment", "exposed_users": 500, "buyer_users": 25},
        ],
        activity_days=10,
        mde_pp=1.0,
        traffic_share=0.5,
    )

    assert plan["status"] == "draft_requires_confirmation"
    assert plan["baseline_rate"] == pytest.approx(0.05)
    assert plan["target_rate"] == pytest.approx(0.06)
    assert plan["required_total"] == plan["required_per_group"] * 2
    assert plan["estimated_days"] == pytest.approx(-(-plan["required_total"] // 50))
    assert plan["feasibility"]["status"] == "exceeds_observed_window"
    assert plan["feasibility"]["reference_window_days"] == 10
    assert plan["feasibility"]["required_windows"] > 1
    assert {item["status"] for item in plan["readiness"]} == {
        "user_confirmed_input",
        "requires_validation",
        "requires_business_threshold",
    }


def test_experiment_plan_rejects_impossible_target_rate():
    with pytest.raises(ValueError, match="target conversion rate"):
        plan_binary_experiment(
            [{"variant": "all", "exposed_users": 100, "buyer_users": 95}],
            activity_days=7,
            mde_pp=10,
        )


def test_plan_marks_a_same_window_experiment_as_feasible():
    plan = plan_binary_experiment(
        [{"variant": "all", "exposed_users": 10000, "buyer_users": 500}],
        activity_days=10,
        mde_pp=5,
    )
    assert plan["feasibility"]["status"] == "within_observed_window"
    assert plan["feasibility"]["required_windows"] == 1


def test_returned_randomized_outcome_requires_plan_and_reaches_business_threshold():
    result = assess_binary_outcome(
        control_total=10_000,
        control_successes=500,
        treatment_total=10_000,
        treatment_successes=650,
        measurement_method="randomized_experiment",
        randomization_verified=True,
        guardrail_status="passed",
        required_total=18_000,
        mde_pp=1.0,
    )

    assert result["status"] == "decision_threshold_met"
    assert result["causal_readout"] is True
    assert result["difference_pp"] == 1.5
    assert result["sample_coverage"] == 20_000 / 18_000


def test_returned_outcome_blocks_peeking_before_planned_sample():
    result = assess_binary_outcome(
        control_total=2_000,
        control_successes=100,
        treatment_total=2_000,
        treatment_successes=140,
        measurement_method="randomized_experiment",
        randomization_verified=True,
        guardrail_status="passed",
        required_total=18_000,
        mde_pp=1.0,
    )

    assert result["status"] == "planned_sample_not_reached"
    assert result["causal_readout"] is False


def test_nonrandomized_return_is_never_relabelled_as_causal():
    result = assess_binary_outcome(
        control_total=10_000,
        control_successes=500,
        treatment_total=10_000,
        treatment_successes=700,
        measurement_method="before_after",
        randomization_verified=False,
        guardrail_status="passed",
        required_total=None,
        mde_pp=None,
    )

    assert result["status"] == "observational_only"
    assert result["causal_readout"] is False
    assert "不能证明" in result["conclusion"]


def test_returned_outcome_detects_sample_ratio_mismatch():
    result = assess_binary_outcome(
        control_total=2_000,
        control_successes=100,
        treatment_total=3_000,
        treatment_successes=180,
        measurement_method="randomized_experiment",
        randomization_verified=True,
        guardrail_status="passed",
        required_total=5_000,
        mde_pp=1.0,
    )

    assert result["status"] == "sample_ratio_mismatch"
    assert result["allocation_check"] == "sample_ratio_mismatch"
