from datetime import UTC, datetime, timedelta

from analytics_agent.merchant.evaluation import summarize_trace_quality


def valid_trace():
    answer = (
        "目标达成但贡献下降。q_12345678\n已确认事实\n"
        "- 转化率 12.5%，路径与成本已核对。q_12345678\n"
        "解释假设\n优惠可能影响行为。\n待验证项\n随机分流。\n"
        "下一轮行动\n对象为优惠实验组；验证指标为转化率，护栏指标为贡献额，"
        "达到条件继续，否则停止。前后相关变化不能证明活动因果。"
    )
    return [
        {"event_type": "TEXT", "role": "user", "payload": {"text": "复盘"}},
        {
            "event_type": "SQL",
            "role": "assistant",
            "payload": {
                "scope_confirmed": True,
                "scope_id": "scope-a",
                "truncated": False,
                "evidence_id": "q_12345678",
                "target": {"achieved": True},
                "rows": [
                    {"period": "baseline", "contribution_cents": 20000},
                    {"period": "activity", "contribution_cents": 15000},
                ],
            },
        },
        {"event_type": "COMPLETE", "role": "assistant", "payload": {"text": answer}},
    ]


def test_trace_quality_groups_cover_every_gate_and_report_runtime():
    started = datetime.now(UTC)
    messages = valid_trace()
    for index, message in enumerate(messages):
        message["created_at"] = started + timedelta(seconds=index)
    messages.insert(
        -1,
        {
            "event_type": "USAGE",
            "role": "assistant",
            "payload": {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
            "created_at": started + timedelta(seconds=len(messages) - 1),
        },
    )

    summary = summarize_trace_quality(messages)

    assert summary["status"] == "passed"
    assert summary["passed"] == summary["total"] == 41
    assert sum(group["total"] for group in summary["groups"]) == 41
    assert summary["model_calls"] == 1
    assert summary["total_tokens"] == 1200
    assert summary["elapsed_seconds"] is not None
    assert summary["quality_retry"] is None


def test_trace_quality_exposes_the_durable_repair_audit():
    messages = valid_trace()
    messages.append(
        {
            "event_type": "QUALITY_RETRY",
            "role": "assistant",
            "payload": {
                "status": "accepted",
                "attempt": 1,
                "original_passed": 31,
                "repaired_passed": 36,
                "total": 36,
                "remaining_failed_checks": [],
            },
        }
    )

    summary = summarize_trace_quality(messages)

    assert summary["quality_retry"]["status"] == "accepted"
    assert summary["quality_retry"]["original_passed"] == 31
