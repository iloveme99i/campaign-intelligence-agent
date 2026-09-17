import importlib.util
from pathlib import Path


def _runner_module():
    path = Path(__file__).parents[2] / "scripts" / "run-merchant-eval-suite.py"
    spec = importlib.util.spec_from_file_location("merchant_eval_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eval_manifest_has_balanced_executable_coverage():
    runner = _runner_module()
    root = Path(__file__).parents[2]
    cases = runner.load_cases(root / "evals" / "merchant_review_cases.jsonl")
    matrix = runner.coverage(cases)

    assert matrix["cases"] == 54
    assert set(matrix["campaigns"].values()) == {18}
    assert min(matrix["decision_intents"].values()) >= 13
    assert min(matrix["risk_focus"].values()) >= 13
    assert sum(matrix["filtered_scopes"].values()) == 6


def test_case_assessment_checks_route_answer_and_quality():
    runner = _runner_module()
    root = Path(__file__).parents[2]
    case = runner.load_cases(root / "evals" / "merchant_review_cases.jsonl")[0]
    topics = "；".join(case["oracle"]["required_answer_topics"])
    dimension = case["oracle"]["allowed_diagnostics"][0]
    conversation = {
        "messages": [
            {
                "event_type": "TOOL_CALL",
                "payload": {"tool_name": "compare_periods", "tool_input": {}},
            },
            {
                "event_type": "TOOL_CALL",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "tool_input": {"dimension": dimension},
                },
            },
            {
                "event_type": "COMPLETE",
                "payload": {"text": f"{topics}；前后变化不能证明活动因果。"},
            },
        ]
    }

    result = runner.assess_case(case, conversation, {"status": "passed"})

    assert result["passed"] is True
    assert all(result["assertions"].values())


def test_case_assessment_accepts_semantic_topic_equivalents():
    runner = _runner_module()
    root = Path(__file__).parents[2]
    cases = runner.load_cases(root / "evals" / "merchant_review_cases.jsonl")
    case = next(item for item in cases if item["task"]["decision_intent"] == "scale")
    dimension = case["oracle"]["allowed_diagnostics"][0]
    conversation = {
        "messages": [
            {
                "event_type": "TOOL_CALL",
                "payload": {"tool_name": "compare_periods", "tool_input": {}},
            },
            {
                "event_type": "TOOL_CALL",
                "payload": {
                    "tool_name": "diagnose_dimension",
                    "tool_input": {"dimension": dimension},
                },
            },
            {
                "event_type": "COMPLETE",
                "payload": {
                    "text": (
                        "核心指标已核对；贡献利润为正；放量条件是连续两周稳定；"
                        "止损线为退货率 8%。观察性结果不等于因果。"
                    )
                },
            },
        ]
    }

    result = runner.assess_case(case, conversation, {"status": "passed"})

    assert result["passed"] is True
    assert result["assertions"]["answer_topics"] is True
    assert result["assertions"]["causal_limit"] is True


def test_result_summary_exposes_reliability_cost_and_latency():
    runner = _runner_module()
    results = [
        {
            "passed": True,
            "assertions": {"answer_topics": True, "quality_status": True},
            "quality": {
                "failed_checks": [],
                "quality_retry": None,
                "model_calls": 2,
                "total_tokens": 30_000,
                "elapsed_seconds": 10.0,
            },
        },
        {
            "passed": False,
            "assertions": {"answer_topics": False, "quality_status": False},
            "quality": {
                "failed_checks": ["money_values_grounded"],
                "quality_retry": {"attempted": True},
                "model_calls": 3,
                "total_tokens": 42_000,
                "elapsed_seconds": 20.0,
            },
        },
    ]

    summary = runner.summarize_results(results)

    assert summary["pass_rate"] == 0.5
    assert summary["assertion_failure_counts"] == {
        "answer_topics": 1,
        "quality_status": 1,
    }
    assert summary["quality_failure_counts"] == {"money_values_grounded": 1}
    assert summary["repair_attempts"] == 1
    assert summary["model_calls"] == 5
    assert summary["tokens"]["total"] == 72_000
    assert summary["latency_seconds"] == {
        "mean": 15.0,
        "p50": 15.0,
        "p95": 19.5,
        "max": 20.0,
    }
