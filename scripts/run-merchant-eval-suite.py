#!/usr/bin/env python3
"""Validate or execute the 54-case merchant Agent evaluation suite.

Validation is local and free. Real execution is opt-in because it calls the
configured model once or more per case and may consume paid API quota.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

import httpx
from analytics_agent.merchant.evaluation import money_grounding_issues

REQUIRED_CASE_KEYS = {"id", "data_origin", "prompt", "scope", "task", "oracle"}
VALID_INTENTS = {"continue", "adjust", "scale", "stop"}
VALID_RISKS = {"goal", "path", "variant", "cost"}
VALID_DIMENSIONS = {"variant", "channel", "location_id", "audience"}
TOPIC_TERMS = {
    "目标": ("目标", "主指标", "核心指标", "KPI"),
    "路径": ("路径", "漏斗", "承接"),
    "成本": ("成本", "投入", "费用"),
    "继续条件": ("继续条件", "继续前提", "续投条件"),
    "最低承接节点": ("最低承接节点", "最弱承接", "最大漏损", "瓶颈节点"),
    "调整对象": ("调整对象", "优先调整", "优先修正"),
    "验证条件": ("验证条件", "验证指标", "验证窗口", "判定条件", "随机验证"),
    "贡献额": ("贡献额", "贡献利润", "毛利"),
    "扩大条件": (
        "扩大条件",
        "扩量条件",
        "放量条件",
        "扩大前提",
        "扩大覆盖",
        "扩大投入",
        "扩大时",
    ),
    "护栏": ("护栏", "风险阈值", "止损线", "安全边界"),
    "停止条件": ("停止条件", "停止或继续条件", "停投条件", "暂停条件", "止损条件"),
    "风险": ("风险", "不确定性", "证据边界", "未验证", "区间跨 0", "未分摊"),
}
CAUSAL_LIMIT_PATTERNS = (
    re.compile(r"(?:不能|无法|不足以).{0,16}(?:证明|确认|推断).{0,12}因果"),
    re.compile(r"(?:相关|前后变化|观察性结果).{0,12}(?:不等于|不代表|不能证明).{0,8}因果"),
    re.compile(r"因果.{0,12}(?:不成立|未证实|无法确认|不能确认|证据不足)"),
)


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if len(cases) < 50:
        raise ValueError(f"expected at least 50 cases, found {len(cases)}")
    ids = [case.get("id") for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("case ids must be unique")
    for index, case in enumerate(cases, 1):
        missing = REQUIRED_CASE_KEYS - case.keys()
        if missing:
            raise ValueError(f"case {index} missing keys: {sorted(missing)}")
        if case["task"].get("decision_intent") not in VALID_INTENTS:
            raise ValueError(f"case {case['id']} has invalid decision intent")
        if case["task"].get("risk_focus") not in VALID_RISKS:
            raise ValueError(f"case {case['id']} has invalid risk focus")
        allowed = set(case["oracle"].get("allowed_diagnostics", []))
        if not allowed or not allowed.issubset(VALID_DIMENSIONS):
            raise ValueError(f"case {case['id']} has invalid diagnostic oracle")
        if case.get("data_origin") != "deterministic_synthetic_fixture":
            raise ValueError(f"case {case['id']} must declare synthetic data origin")
    return cases


def coverage(cases: list[dict[str, Any]]) -> dict[str, Any]:
    campaigns = Counter(case["scope"]["campaign_id"] for case in cases)
    intents = Counter(case["task"]["decision_intent"] for case in cases)
    risks = Counter(case["task"]["risk_focus"] for case in cases)
    filtered = Counter(
        key
        for case in cases
        for key in VALID_DIMENSIONS
        if case["scope"].get(key) is not None
    )
    return {
        "cases": len(cases),
        "campaigns": dict(sorted(campaigns.items())),
        "decision_intents": dict(sorted(intents.items())),
        "risk_focus": dict(sorted(risks.items())),
        "filtered_scopes": dict(sorted(filtered.items())),
    }


def _latest_answer(messages: list[dict[str, Any]]) -> str:
    return next(
        (
            str(message.get("payload", {}).get("text", ""))
            for message in reversed(messages)
            if message.get("event_type") == "COMPLETE"
        ),
        "",
    )


def _topic_present(answer: str, topic: str) -> bool:
    """Accept semantic equivalents without rewarding arbitrary loose wording."""

    return any(term.casefold() in answer.casefold() for term in TOPIC_TERMS.get(topic, (topic,)))


def _states_causal_limit(answer: str) -> bool:
    observational_limit = any(pattern.search(answer) for pattern in CAUSAL_LIMIT_PATTERNS)
    conditional_identification = bool(
        re.search(r"DiD|双重差分|ATT", answer, flags=re.IGNORECASE)
        and re.search(r"条件性|成立前提|平行趋势|同期对照", answer)
    )
    return observational_limit or conditional_identification


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)


def assess_case(
    case: dict[str, Any],
    conversation: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    messages = conversation.get("messages", [])
    tool_calls = [
        message.get("payload", {})
        for message in messages
        if message.get("event_type") == "TOOL_CALL"
    ]
    tool_names = [str(call.get("tool_name", "")) for call in tool_calls]
    diagnostic_dimensions = [
        str(call.get("tool_input", {}).get("dimension", ""))
        for call in tool_calls
        if call.get("tool_name") == "diagnose_dimension"
    ]
    answer = _latest_answer(messages)
    confirmed_payloads = [
        message.get("payload", {})
        for message in messages
        if message.get("event_type") == "SQL"
        and message.get("payload", {}).get("scope_confirmed")
    ]
    grounding_payload: dict[str, Any] = confirmed_payloads[-1] if confirmed_payloads else {}
    if grounding_payload:
        grounding_payload = {
            **grounding_payload,
            "_diagnostic_evidence": [
                message.get("payload", {})
                for message in messages
                if message.get("event_type") == "SQL"
                and message.get("payload", {}).get("tool_name") == "diagnose_dimension"
                and message.get("payload", {}).get("scope_id")
                == grounding_payload.get("scope_id")
            ],
        }
    oracle = case["oracle"]
    assertions = {
        "first_tool": bool(tool_names) and tool_names[0] == oracle["first_tool"],
        "diagnosis_present": oracle["required_diagnostic"] in tool_names,
        "diagnosis_allowed": bool(diagnostic_dimensions)
        and set(diagnostic_dimensions).issubset(set(oracle["allowed_diagnostics"])),
        "diagnosis_bounded": len(diagnostic_dimensions) <= int(oracle["max_diagnostics"]),
        "answer_topics": all(
            _topic_present(answer, topic) for topic in oracle["required_answer_topics"]
        ),
        "causal_limit": _states_causal_limit(answer),
        "quality_status": quality.get("status") == oracle["required_quality_status"],
    }
    return {
        "id": case["id"],
        "passed": all(assertions.values()),
        "assertions": assertions,
        "diagnostic_dimensions": diagnostic_dimensions,
        "answer": answer,
        "answer_excerpt": answer[:1200],
        "failure_details": {
            "money_grounding_issues": money_grounding_issues(answer, grounding_payload)
            if grounding_payload and "money_values_grounded" in quality.get("failed_checks", [])
            else []
        },
        "quality": quality,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    assertion_failures = Counter(
        name
        for result in results
        for name, passed in result.get("assertions", {}).items()
        if not passed
    )
    quality_failures = Counter(
        str(check)
        for result in results
        for check in result.get("quality", {}).get("failed_checks", [])
    )
    quality = [result.get("quality", {}) for result in results]
    elapsed = [float(item["elapsed_seconds"]) for item in quality if item.get("elapsed_seconds")]
    tokens = [int(item["total_tokens"]) for item in quality if item.get("total_tokens")]
    return {
        "pass_rate": round(
            sum(bool(result.get("passed")) for result in results) / len(results), 4
        )
        if results
        else 0.0,
        "assertion_failure_counts": dict(assertion_failures.most_common()),
        "quality_failure_counts": dict(quality_failures.most_common()),
        "repair_attempts": sum(item.get("quality_retry") is not None for item in quality),
        "model_calls": sum(int(item.get("model_calls") or 0) for item in quality),
        "tokens": {
            "total": sum(tokens),
            "mean_per_case": round(mean(tokens), 1) if tokens else None,
            "median_per_case": round(median(tokens), 1) if tokens else None,
        },
        "latency_seconds": {
            "mean": round(mean(elapsed), 3) if elapsed else None,
            "p50": _percentile(elapsed, 0.5),
            "p95": _percentile(elapsed, 0.95),
            "max": round(max(elapsed), 3) if elapsed else None,
        },
    }


async def execute_suite(
    cases: list[dict[str, Any]],
    *,
    base_url: str,
    keep_conversations: bool,
    keep_failures: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    timeout = httpx.Timeout(connect=10, read=240, write=30, pool=10)
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=timeout, trust_env=False
    ) as client:
        settings = (await client.get("/api/settings/llm")).raise_for_status().json()
        if not settings.get("has_key"):
            raise RuntimeError("model API key is not configured")
        runtime = {
            "provider": settings.get("provider"),
            "model": settings.get("openai_compatible_model") or settings.get("model"),
            "base_url": settings.get("base_url"),
            "prompt_cache_enabled": bool(settings.get("enable_prompt_cache")),
        }
        snapshot_response = await client.post("/api/merchant/examples")
        snapshot_response.raise_for_status()
        engine_name = snapshot_response.json()["engine_name"]
        results = []
        for index, case in enumerate(cases, 1):
            created = await client.post(
                "/api/conversations",
                json={"title": f"评测 · {case['id']}", "engine_name": engine_name},
            )
            created.raise_for_status()
            conversation_id = created.json()["id"]
            case_passed = False
            try:
                response = await client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    json={
                        "text": case["prompt"],
                        "review_scope": case["scope"],
                        "review_task": case["task"],
                    },
                )
                response.raise_for_status()
                conversation_response = await client.get(
                    f"/api/conversations/{conversation_id}"
                )
                conversation_response.raise_for_status()
                quality_response = await client.get(
                    f"/api/merchant/conversations/{conversation_id}/trace-quality"
                )
                quality_response.raise_for_status()
                result = assess_case(
                    case, conversation_response.json(), quality_response.json()
                )
                result["conversation_id"] = conversation_id
                result["ordinal"] = index
                results.append(result)
                case_passed = bool(result["passed"])
                print(
                    f"[{index:02d}/{len(cases)}] {'PASS' if result['passed'] else 'FAIL'} "
                    f"{case['id']}"
                )
            except Exception as exc:
                results.append(
                    {
                        "id": case["id"],
                        "ordinal": index,
                        "conversation_id": conversation_id,
                        "passed": False,
                        "assertions": {"execution_completed": False},
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(f"[{index:02d}/{len(cases)}] ERROR {case['id']}: {exc}")
            finally:
                should_delete = not keep_conversations and (case_passed or not keep_failures)
                if should_delete:
                    await client.delete(f"/api/conversations/{conversation_id}")
        return results, runtime


def main() -> int:
    root = Path(__file__).parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", type=Path, default=root / "evals" / "merchant_review_cases.jsonl"
    )
    parser.add_argument("--execute", action="store_true", help="Call the configured model")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Execute a named case; repeat to build a representative subset",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8101")
    parser.add_argument("--keep-conversations", action="store_true")
    parser.add_argument(
        "--keep-failures",
        action="store_true",
        help="Keep only failed evaluation conversations for interactive debugging",
    )
    parser.add_argument(
        "--output", type=Path, default=root / "evals" / "latest-suite-run.json"
    )
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.case_id and args.limit is not None:
        parser.error("--case-id and --limit cannot be used together")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.case_id:
        by_id = {case["id"]: case for case in cases}
        unknown = [case_id for case_id in args.case_id if case_id not in by_id]
        if unknown:
            parser.error(f"unknown case ids: {', '.join(unknown)}")
        selected = [by_id[case_id] for case_id in dict.fromkeys(args.case_id)]
    else:
        selected = cases[: args.limit] if args.limit else cases
    full_coverage = coverage(cases)
    selected_coverage = coverage(selected)
    print(json.dumps(full_coverage, ensure_ascii=False, indent=2))
    if not args.execute:
        print("validation only; pass --execute to run paid model calls")
        return 0

    results, runtime = asyncio.run(
        execute_suite(
            selected,
            base_url=args.base_url,
            keep_conversations=args.keep_conversations,
            keep_failures=args.keep_failures,
        )
    )
    report = {
        "schema_version": 2,
        "run_at": datetime.now(UTC).isoformat(),
        "case_file": str(args.cases),
        "runtime": runtime,
        "coverage": {
            "manifest": full_coverage,
            "executed": selected_coverage,
        },
        "executed": len(results),
        "passed": sum(bool(result["passed"]) for result in results),
        "summary": summarize_results(results),
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote results to {args.output}")
    return 0 if report["passed"] == report["executed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
