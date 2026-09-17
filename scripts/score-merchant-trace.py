#!/usr/bin/env python3
"""Score one persisted merchant review without sending data to a model."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from analytics_agent.merchant.evaluation import score_trace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("conversation_id")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(".merchant-runtime/data/agent.db"),
    )
    parser.add_argument(
        "--allow-missing-answer",
        action="store_true",
        help="Only score deterministic trace gates.",
    )
    args = parser.parse_args()

    connection = sqlite3.connect(args.database)
    rows = connection.execute(
        """SELECT event_type, role, payload
           FROM messages
           WHERE conversation_id = ?
           ORDER BY sequence""",
        (args.conversation_id,),
    ).fetchall()
    if not rows:
        parser.error("conversation not found or contains no messages")

    messages = [
        {"event_type": event_type, "role": role, "payload": json.loads(payload)}
        for event_type, role, payload in rows
    ]
    result = score_trace(messages, require_answer=not args.allow_missing_answer)
    print(
        json.dumps(
            {
                "conversation_id": args.conversation_id,
                "passed": result.passed,
                "score": round(result.score, 3),
                "checks": result.checks,
                "evidence_ids": result.evidence_ids,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
