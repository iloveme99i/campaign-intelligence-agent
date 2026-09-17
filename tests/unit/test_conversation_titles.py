from types import SimpleNamespace

import orjson
from analytics_agent.api.conversations import _clean_title, _merchant_title


def test_clean_title_removes_model_markup_and_invisible_characters():
    assert _clean_title("## 标题：\u200b全域大促复盘 **\n解释", "活动决策") == "全域大促复盘"


def test_clean_title_rejects_corrupted_output():
    assert _clean_title("��***", "活动决策") == "活动决策"


def test_merchant_title_comes_from_campaign_and_structured_decision():
    message = SimpleNamespace(
        role="user",
        event_type="TEXT",
        payload=orjson.dumps(
            {
                "text": "复盘「字节系 · 全域大促」。本轮需要决定：是否扩量。",
                "review_task": {"decision_intent": "scale"},
            }
        ),
    )

    assert _merchant_title([message]) == "字节系 · 全域大促｜扩量判断"
