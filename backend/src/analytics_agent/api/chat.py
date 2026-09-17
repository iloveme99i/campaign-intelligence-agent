from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import orjson
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from opentelemetry import trace as _otrace
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from analytics_agent.agent.analysis import CONTEXT_TOOLS
from analytics_agent.db.base import get_session
from analytics_agent.db.models import Message
from analytics_agent.db.repository import ConversationRepo, IntegrationRepo, MessageRepo
from analytics_agent.merchant.scope import ReviewScope, ReviewTask

_tracer = _otrace.get_tracer(__name__)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/conversations", tags=["chat"])

# ── Per-conversation live stream registry ─────────────────────────────────────


@dataclass
class ConvStream:
    task: asyncio.Task | None
    replay: list[dict] = field(default_factory=list)
    subs: list[asyncio.Queue] = field(default_factory=list)
    done: bool = False


_active_streams: dict[str, ConvStream] = {}

# ── Background quality computation ────────────────────────────────────────────
_QUALITY_MIN_INTERVAL = 30.0
_quality_throttle: dict[str, float] = {}
_quality_last_count: dict[str, int] = {}
_context_call_counts: dict[str, int] = {}


def _should_persist_stream_event(event: dict) -> bool:
    """Persist durable events, not transient token chunks used only for live rendering."""
    return event.get("event") not in (None, "KEEPALIVE", "TEXT")


def _format_error(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _format_error(exc.exceptions[0])
    return f"{exc.__class__.__name__}: {exc}"


def _maybe_schedule_quality(conv_id: str, factory) -> None:
    now = time.monotonic()
    current = _context_call_counts.get(conv_id, 0)
    if current <= _quality_last_count.get(conv_id, 0):
        return
    if now - _quality_throttle.get(conv_id, 0) < _QUALITY_MIN_INTERVAL:
        return
    _quality_throttle[conv_id] = now
    _quality_last_count[conv_id] = current
    asyncio.create_task(_compute_quality_background(conv_id, factory))


async def _compute_quality_background(conv_id: str, factory) -> None:
    try:
        from analytics_agent.agent.analysis import compute_context_quality

        async with factory() as session:
            messages = await MessageRepo(session).list_for_conversation(conv_id)
            quality = await compute_context_quality(messages)
            await ConversationRepo(session).update_quality(
                conv_id, quality.score, quality.label, quality.breakdown.get("reason", "")
            )
    except Exception as exc:
        logger.debug("Background quality update failed for %s: %s", conv_id, exc)


class ChatMessageRequest(BaseModel):
    text: str
    review_scope: ReviewScope | None = None
    review_task: ReviewTask | None = None


async def _persist_message(
    session: AsyncSession,
    conversation_id: str,
    event_type: str,
    role: str,
    payload: dict,
    sequence: int,
) -> None:
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        event_type=event_type,
        role=role,
        payload=orjson.dumps(payload).decode(),
        sequence=sequence,
        created_at=datetime.now(UTC),
    )
    await MessageRepo(session).create(msg)
    await ConversationRepo(session).touch(conversation_id)


async def _run_and_broadcast(
    conversation_id: str,
    stream: ConvStream,
    user_text: str,
    engine_name: str,
    keepalive_interval: int,
    review_scope: ReviewScope | None = None,
    scope_source: str = "explicit",
    review_task: ReviewTask | None = None,
    task_source: str = "explicit",
) -> None:
    """
    Background task: runs the full agent pipeline independently of the HTTP
    connection. Commits each event immediately so switch-back sees them in DB.
    Fans out to all live SSE subscribers via ConvStream.
    """
    from analytics_agent.db.base import _get_session_factory

    factory = _get_session_factory()

    def _broadcast(evt: dict) -> None:
        stream.replay.append(evt)
        for q in list(stream.subs):
            q.put_nowait(evt)

    try:
        async with factory() as session:
            msg_repo = MessageRepo(session)
            sequence = await msg_repo.next_sequence(conversation_id)

            await _persist_message(
                session,
                conversation_id,
                "TEXT",
                "user",
                {
                    "text": user_text,
                    **(
                        {
                            "review_scope": review_scope.model_dump(mode="json"),
                            "scope_id": review_scope.scope_id,
                            "scope_source": scope_source,
                        }
                        if review_scope
                        else {}
                    ),
                    **(
                        {
                            "review_task": review_task.model_dump(mode="json"),
                            "task_id": review_task.task_id,
                            "task_source": task_source,
                        }
                        if review_task
                        else {}
                    ),
                },
                sequence,
            )
            await session.commit()
            sequence += 1

            # ── MOCK_LLM ──────────────────────────────────────────────────────
            if os.environ.get("MOCK_LLM") == "1":
                from analytics_agent.agent.mock_llm import mock_stream_events

                async for evt in mock_stream_events(conversation_id, user_text):
                    if _should_persist_stream_event(evt):
                        with contextlib.suppress(Exception):
                            await _persist_message(
                                session,
                                conversation_id,
                                evt["event"],
                                "assistant",
                                evt.get("payload", {}),
                                sequence,
                            )
                            await session.commit()
                            sequence += 1
                    _broadcast(evt)
                return

            # ── No engine ─────────────────────────────────────────────────────
            from analytics_agent.engines.factory import list_engines as _list_engines

            if not engine_name or engine_name not in {e["name"] for e in _list_engines()}:
                _msg = (
                    "I'm ready to help, but I don't have a data source connected yet. "
                    "To query your data, add a SQL engine to `config.yaml` "
                    "(see `config.yaml.example` — Snowflake, BigQuery, MySQL, DuckDB and more "
                    "are supported) and restart the server. "
                    "Once a source is connected I can write queries, explore schemas, and "
                    "build visualizations for you."
                )
                _run_id = str(uuid.uuid4())
                for _evt in cast(
                    list[dict[str, Any]],
                    [
                        {
                            "event": "TEXT",
                            "conversation_id": conversation_id,
                            "message_id": _run_id,
                            "payload": {"text": _msg},
                        },
                        {
                            "event": "COMPLETE",
                            "conversation_id": conversation_id,
                            "message_id": str(uuid.uuid4()),
                            "payload": {"text": _msg},
                        },
                    ],
                ):
                    with contextlib.suppress(Exception):
                        await _persist_message(
                            session,
                            conversation_id,
                            _evt["event"],
                            "assistant",
                            _evt["payload"],
                            sequence,
                        )
                        await session.commit()
                        sequence += 1
                    _broadcast(_evt)
                return

            # ── Normal agent path ─────────────────────────────────────────────
            from analytics_agent.agent.compactor_registry import get_compactor
            from analytics_agent.agent.graph import build_graph
            from analytics_agent.agent.history import build_history
            from analytics_agent.agent.streaming import stream_graph_events
            from analytics_agent.config import settings as _settings
            from analytics_agent.context.registry import build_platform
            from analytics_agent.db.repository import ContextPlatformRepo, SettingsRepo
            from analytics_agent.engines.mcp.engine import MCPQueryEngine
            from analytics_agent.engines.resolver import resolve_engine

            is_merchant_engine = False
            prior_messages = await msg_repo.list_for_conversation(conversation_id)
            history = build_history(
                prior_messages[:-1],
                user_text,
                compactor=get_compactor(),
                max_history_tokens=_settings.max_history_tokens,
            )

            settings_repo = SettingsRepo(session)
            custom_prompt = await settings_repo.get("system_prompt")
            disabled_raw = await settings_repo.get("disabled_tools")
            disabled_tools: set[str] = set()
            if disabled_raw:
                with contextlib.suppress(Exception):
                    disabled_tools = set(orjson.loads(disabled_raw))

            mutations_raw = await settings_repo.get("enabled_mutation_tools")
            enabled_mutations: set[str] = set()
            if mutations_raw:
                with contextlib.suppress(Exception):
                    enabled_mutations = set(orjson.loads(mutations_raw))

            disabled_conns_raw = await settings_repo.get("disabled_connections")
            disabled_connections: set[str] = set()
            if disabled_conns_raw:
                with contextlib.suppress(Exception):
                    disabled_connections = set(orjson.loads(disabled_conns_raw))

            cp_repo = ContextPlatformRepo(session)
            all_cp_rows = await cp_repo.list_all()

            try:
                from analytics_agent.merchant.engine import (
                    MerchantQueryEngine,
                    build_analysis_brief,
                )
                from analytics_agent.merchant.runtime import build_merchant_graph

                engine = await resolve_engine(engine_name, session)
                if isinstance(engine, MerchantQueryEngine):
                    is_merchant_engine = True
                    all_cp_rows = []  # Imported merchant snapshots have no external context tools.
                    if review_scope is not None:
                        from langchain_core.messages import AIMessage, ToolMessage

                        engine.confirmed_scope = review_scope
                        call_id = str(uuid.uuid4())
                        args = {"scope": review_scope.model_dump(mode="json")}
                        result_text = await engine.comparison_tool().ainvoke(args)
                        result = orjson.loads(result_text)
                        if "error" in result:
                            raise ValueError("已确认口径计算失败：" + result["error"])
                        events = [
                            {
                                "event": "TOOL_CALL",
                                "payload": {
                                    "tool_name": "compare_periods",
                                    "tool_input": args,
                                    "tool_run_id": call_id,
                                },
                            },
                            {
                                "event": "SQL",
                                "payload": {
                                    **result,
                                    "tool_name": "compare_periods",
                                    "tool_run_id": call_id,
                                    "scope_confirmed": True,
                                    "scope_source": scope_source,
                                },
                            },
                        ]
                        for evt in events:
                            await _persist_message(
                                session,
                                conversation_id,
                                evt["event"],
                                "assistant",
                                evt["payload"],
                                sequence,
                            )
                            await session.commit()
                            sequence += 1
                            _broadcast(
                                {
                                    **evt,
                                    "conversation_id": conversation_id,
                                    "message_id": str(uuid.uuid4()),
                                }
                            )
                        model_result_text = orjson.dumps(
                            build_analysis_brief(result, review_task)
                        ).decode()
                        history.extend(
                            [
                                AIMessage(
                                    content="",
                                    tool_calls=[
                                        {"id": call_id, "name": "compare_periods", "args": args}
                                    ],
                                ),
                                ToolMessage(
                                    content=model_result_text,
                                    name="compare_periods",
                                    tool_call_id=call_id,
                                ),
                            ]
                        )
                context_tools: list = []
                include_mutations = bool(enabled_mutations)
                for row in all_cp_rows:
                    platform = build_platform(
                        row,
                        disabled_connections=disabled_connections,
                        include_mutations=include_mutations,
                    )
                    if platform is None:
                        continue
                    tools = await platform.get_tools()
                    context_tools.extend(tools)

                logger.info(
                    "Total context_tools=%d for conversation %s",
                    len(context_tools),
                    conversation_id,
                )
                engine_tools = None
                if isinstance(engine, MCPQueryEngine):
                    mcp_tools = await engine.get_tools_async()
                    engine_tools = [t for t in mcp_tools if t.name not in disabled_tools]

                graph = (
                    build_merchant_graph(engine)
                    if isinstance(engine, MerchantQueryEngine)
                    else build_graph(
                        engine=engine if not isinstance(engine, MCPQueryEngine) else None,
                        engine_name=engine_name,
                        system_prompt_override=custom_prompt,
                        disabled_tools=disabled_tools,
                        enabled_mutations=enabled_mutations,
                        context_tools=context_tools,
                        engine_tools=engine_tools,
                    )
                )
            except Exception as exc:
                for _evt in cast(
                    list[dict[str, Any]],
                    [
                        {
                            "event": "ERROR",
                            "conversation_id": conversation_id,
                            "message_id": str(uuid.uuid4()),
                            "payload": {"error": _format_error(exc)},
                        },
                        {
                            "event": "COMPLETE",
                            "conversation_id": conversation_id,
                            "message_id": str(uuid.uuid4()),
                            "payload": {"text": ""},
                        },
                    ],
                ):
                    with contextlib.suppress(Exception):
                        await _persist_message(
                            session,
                            conversation_id,
                            _evt["event"],
                            "assistant",
                            _evt["payload"],
                            sequence,
                        )
                        await session.commit()
                        sequence += 1
                    _broadcast(_evt)
                return

            async for evt in stream_graph_events(
                graph=graph,
                user_text=user_text,
                conversation_id=conversation_id,
                engine_name=engine_name,
                keepalive_interval=keepalive_interval,
                history=history,
            ):
                if _should_persist_stream_event(evt):
                    with contextlib.suppress(Exception):
                        await _persist_message(
                            session,
                            conversation_id,
                            evt["event"],
                            "assistant",
                            evt.get("payload", {}),
                            sequence,
                        )
                        await session.commit()
                        sequence += 1

                    if evt.get("event") == "TOOL_RESULT":
                        tool_name = evt.get("payload", {}).get("tool_name", "")
                        if tool_name in CONTEXT_TOOLS:
                            _context_call_counts[conversation_id] = (
                                _context_call_counts.get(conversation_id, 0) + 1
                            )
                            _maybe_schedule_quality(conversation_id, factory)

                    # Telemetry spans — annotate once, emit to both OTEL and Mixpanel.
                    _evt_type = evt.get("event")
                    if _evt_type == "SQL":
                        _payload = evt.get("payload", {})
                        _intg = await IntegrationRepo(session).get(engine_name)
                        _engine_type = _intg.type if _intg else engine_name
                        with _tracer.start_as_current_span("query.completed") as _span:
                            _span.set_attribute("engine.type", _engine_type)
                            _span.set_attribute("row.count", len(_payload.get("rows", [])))
                            _span.set_attribute("query.success", True)
                    elif (
                        _evt_type == "TOOL_RESULT"
                        and evt.get("payload", {}).get("tool_name") == "execute_sql"
                        and evt.get("payload", {}).get("is_error")
                    ):
                        _intg = await IntegrationRepo(session).get(engine_name)
                        _engine_type = _intg.type if _intg else engine_name
                        with _tracer.start_as_current_span("query.completed") as _span:
                            _span.set_attribute("engine.type", _engine_type)
                            _span.set_attribute("query.success", False)
                    elif _evt_type == "CHART":
                        _payload = evt.get("payload", {})
                        _chart_type = _payload.get("chart_type") or ""
                        if not _chart_type:
                            _mark = (_payload.get("vega_lite_spec") or {}).get("mark") or "unknown"
                            _chart_type = (
                                _mark.get("type", "unknown")
                                if isinstance(_mark, dict)
                                else str(_mark)
                            )
                        with _tracer.start_as_current_span("chart.generated") as _span:
                            _span.set_attribute("chart.type", _chart_type)

                _broadcast(evt)

            # Merchant answers get at most two bounded repair passes. Each pass
            # receives only the persisted evidence packet and cannot query or
            # change scope. Structural failures (missing diagnosis, truncated
            # query, mixed scope) are never disguised by rewriting prose.
            if is_merchant_engine:
                try:
                    from analytics_agent.merchant.quality_repair import repair_answer_once

                    stored = await msg_repo.list_for_conversation(conversation_id)
                    records = [
                        {
                            "role": message.role,
                            "event_type": message.event_type,
                            "payload": orjson.loads(message.payload),
                            "created_at": message.created_at,
                        }
                        for message in stored
                    ]
                    repair = await repair_answer_once(records)
                    if repair is not None:
                        for repair_attempt, repair_usage in enumerate(repair.usage_events, start=1):
                            usage_payload = {
                                "input_tokens": int(repair_usage.get("input_tokens", 0) or 0),
                                "output_tokens": int(repair_usage.get("output_tokens", 0) or 0),
                                "total_tokens": int(repair_usage.get("total_tokens", 0) or 0),
                                "cache_read_tokens": 0,
                                "cache_creation_tokens": 0,
                                "node": f"quality_repair_{repair_attempt}",
                            }
                            await _persist_message(
                                session,
                                conversation_id,
                                "USAGE",
                                "assistant",
                                usage_payload,
                                sequence,
                            )
                            await session.commit()
                            sequence += 1
                            _broadcast(
                                {
                                    "event": "USAGE",
                                    "conversation_id": conversation_id,
                                    "message_id": str(uuid.uuid4()),
                                    "payload": usage_payload,
                                }
                            )
                        if repair.accepted:
                            complete_payload = {
                                "text": repair.answer,
                                "quality_retry": True,
                            }
                            await _persist_message(
                                session,
                                conversation_id,
                                "COMPLETE",
                                "assistant",
                                complete_payload,
                                sequence,
                            )
                            await session.commit()
                            sequence += 1
                            _broadcast(
                                {
                                    "event": "COMPLETE",
                                    "conversation_id": conversation_id,
                                    "message_id": str(uuid.uuid4()),
                                    "payload": complete_payload,
                                }
                            )
                        retry_payload = {
                            "status": "accepted" if repair.accepted else "rejected",
                            "attempt": repair.attempts,
                            "original_passed": sum(repair.original_score.checks.values()),
                            "repaired_passed": sum(repair.repaired_score.checks.values()),
                            "total": len(repair.original_score.checks),
                            "remaining_failed_checks": [
                                key
                                for key, passed in repair.repaired_score.checks.items()
                                if not passed
                            ],
                        }
                        await _persist_message(
                            session,
                            conversation_id,
                            "QUALITY_RETRY",
                            "assistant",
                            retry_payload,
                            sequence,
                        )
                        await session.commit()
                        sequence += 1
                        _broadcast(
                            {
                                "event": "QUALITY_RETRY",
                                "conversation_id": conversation_id,
                                "message_id": str(uuid.uuid4()),
                                "payload": retry_payload,
                            }
                        )
                except Exception:
                    # The primary answer remains available, but a failed repair
                    # must be diagnosable from server logs instead of silently
                    # disappearing as if the quality loop had not run.
                    logger.exception("Merchant quality repair failed for %s", conversation_id)

            _context_call_counts.pop(conversation_id, None)
            _quality_last_count.pop(conversation_id, None)

            with contextlib.suppress(Exception):
                from analytics_agent.agent.analysis import compute_context_quality

                all_messages = await msg_repo.list_for_conversation(conversation_id)
                quality = await compute_context_quality(all_messages)
                await ConversationRepo(session).update_quality(
                    conversation_id,
                    quality.score,
                    quality.label,
                    quality.breakdown.get("reason", ""),
                )

    except Exception as exc:
        logger.error("Agent worker failed for %s: %s", conversation_id, exc, exc_info=True)
    finally:
        stream.done = True
        for q in list(stream.subs):
            q.put_nowait(None)
        _active_streams.pop(conversation_id, None)


async def _sse_for_stream(stream: ConvStream, keepalive_interval: int) -> AsyncIterator[str]:
    """
    SSE generator: yields catch-up replay events then tails live queue.
    Exits cleanly on client disconnect — background task keeps running.
    """
    from analytics_agent.agent.streaming import to_sse

    q: asyncio.Queue = asyncio.Queue()
    # Snapshot replay and register subscriber atomically (asyncio is single-threaded)
    replay_snapshot = list(stream.replay)
    if not stream.done:
        stream.subs.append(q)

    try:
        for evt in replay_snapshot:
            yield to_sse(evt)

        if stream.done:
            return

        while True:
            try:
                evt = await asyncio.wait_for(q.get(), timeout=keepalive_interval)
                if evt is None:
                    break
                yield to_sse(evt)
            except TimeoutError:
                yield to_sse({"event": "KEEPALIVE"})
    except GeneratorExit:
        pass
    finally:
        with contextlib.suppress(ValueError):
            stream.subs.remove(q)


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: ChatMessageRequest,
    session: AsyncSession = Depends(get_session),
):
    from analytics_agent.config import settings

    conv = await ConversationRepo(session).get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="Message text cannot be empty")

    if (
        body.review_scope is not None or body.review_task is not None
    ) and not conv.engine_name.startswith("merchant_"):
        raise HTTPException(status_code=422, detail="复盘任务与口径仅适用于商家数据")
    effective_scope = body.review_scope
    scope_source = "explicit"
    if effective_scope is None and conv.engine_name.startswith("merchant_"):
        from analytics_agent.merchant.scope import latest_confirmed_scope

        try:
            effective_scope = latest_confirmed_scope(conv.messages)
        except ValueError as exc:
            raise HTTPException(409, "已保存口径无法读取，请重新确认口径") from exc
        scope_source = "inherited"
    effective_task = body.review_task
    task_source = "explicit"
    if effective_task is None and conv.engine_name.startswith("merchant_"):
        from analytics_agent.merchant.scope import latest_review_task

        try:
            effective_task = latest_review_task(conv.messages)
        except ValueError as exc:
            raise HTTPException(409, "已保存任务无法读取，请重新定义复盘任务") from exc
        task_source = "inherited"
    # No await between checking and registering: one process cannot launch two turns
    # for the same conversation and interleave persisted sequence numbers.
    existing = _active_streams.get(conversation_id)
    if existing is not None and not existing.done:
        raise HTTPException(status_code=409, detail="本轮分析尚未结束，请等待后再发送")

    stream = ConvStream(task=None)
    _active_streams[conversation_id] = stream
    stream.task = asyncio.create_task(
        _run_and_broadcast(
            conversation_id,
            stream,
            body.text.strip(),
            conv.engine_name,
            settings.sse_keepalive_interval,
            effective_scope,
            scope_source,
            effective_task,
            task_source,
        )
    )

    return StreamingResponse(
        _sse_for_stream(stream, settings.sse_keepalive_interval),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.get("/{conversation_id}/stream")
async def reattach_stream(conversation_id: str):
    """Reattach to an in-progress agent stream after switching conversations."""
    from analytics_agent.config import settings

    stream = _active_streams.get(conversation_id)
    if stream is None:
        return Response(status_code=204)
    return StreamingResponse(
        _sse_for_stream(stream, settings.sse_keepalive_interval),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
