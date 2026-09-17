from analytics_agent.api.chat import _should_persist_stream_event


def test_token_chunks_are_live_only_but_complete_answer_is_durable():
    assert not _should_persist_stream_event({"event": "TEXT"})
    assert not _should_persist_stream_event({"event": "KEEPALIVE"})
    assert _should_persist_stream_event({"event": "TOOL_CALL"})
    assert _should_persist_stream_event({"event": "SQL"})
    assert _should_persist_stream_event({"event": "USAGE"})
    assert _should_persist_stream_event({"event": "COMPLETE"})
