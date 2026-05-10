"""FastAPI application — Telegram Intent Processor."""

import asyncio
import json
import os
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import ValidationError

from app.models import (
    BatchResult,
    IncomingMessage,
    MessageRequest,
    ProcessingResult,
    ProcessingStatus,
    SystemHealth,
)
from app.processor import MessageProcessor
from app.store import MessageStore

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT", "50"))

store = MessageStore(redis_url=REDIS_URL)
processor = MessageProcessor(store, max_in_flight=MAX_IN_FLIGHT)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await store.connect()
    yield
    await store.disconnect()


app = FastAPI(title="Telegram Intent Processor", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _aggregate(
    results: list[ProcessingResult], elapsed_ms: int
) -> BatchResult:
    return BatchResult(
        total=len(results),
        processed=sum(
            1 for r in results if r.status == ProcessingStatus.PROCESSED
        ),
        invalid=sum(
            1 for r in results if r.status == ProcessingStatus.INVALID
        ),
        duplicates=sum(
            1 for r in results if r.status == ProcessingStatus.DUPLICATE
        ),
        fallback_writes=sum(1 for r in results if r.fallback_used),
        groups=len(set(r.group_id for r in results)),
        duration_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Core endpoint
# ---------------------------------------------------------------------------

@app.post("/messages")
async def messages(body: MessageRequest) -> dict[str, Any]:
    """Process a single Telegram-style group message."""
    try:
        msg = body.message
    except ValidationError as exc:
        store.invalid_count += 1
        raise HTTPException(status_code=422, detail=json.loads(exc.json()))

    try:
        result = await processor.process(msg)
    except Exception:
        store.invalid_count += 1
        raise HTTPException(status_code=500, detail="Internal processing error")

    return result.model_dump()


@app.post("/messages/batch")
async def messages_batch(body: list[MessageRequest]) -> BatchResult:
    """Process multiple messages concurrently and return summary stats."""
    messages = [req.message for req in body]
    t0 = time.monotonic()
    results = await processor.process_batch(messages)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return _aggregate(results, elapsed_ms)


# ---------------------------------------------------------------------------
# Group state
# ---------------------------------------------------------------------------

@app.get("/groups/{group_id}/state")
async def group_state(group_id: str) -> dict[str, Any]:
    snap = await processor.get_group_snapshot(group_id)
    return {
        "group_id": group_id,
        "snapshot": snap.model_dump(),
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@app.get("/stats")
async def stats() -> dict[str, Any]:
    h = store.health()
    return {
        "total_processed": h["total_processed"],
        "redis_persisted": h["redis_persisted"],
        "fallback_writes": h["fallback_writes"],
        "invalid_messages": h["invalid_messages"],
        "duplicate_messages": h["duplicate_messages"],
        "group_count": len(processor._state_machines),
        "in_flight_current": processor.in_flight_count(),
        "in_flight_max": MAX_IN_FLIGHT,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> SystemHealth:
    h = store.health()
    return SystemHealth(
        redis=h["redis"],
        total_processed=h["total_processed"],
        redis_persisted=h["redis_persisted"],
        fallback_writes=h["fallback_writes"],
        invalid_messages=h["invalid_messages"],
        duplicate_messages=h["duplicate_messages"],
        in_flight_messages=processor.in_flight_count(),
        max_in_flight_limit=MAX_IN_FLIGHT,
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

_DASHBOARD_PATH = Path(__file__).resolve().parent / "dashboard.html"


@app.get("/dashboard")
async def dashboard() -> HTMLResponse:
    html = _DASHBOARD_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/events")
async def sse_events(request: Request) -> StreamingResponse:
    """SSE stream — pushes every processed result to the dashboard."""

    async def event_stream():
        q = processor.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            processor.unsubscribe(q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Demo endpoints
# ---------------------------------------------------------------------------

SAMPLE_TEXTS: list[tuple[str, str]] = [
    ("pricing", "这个多少钱"),
    ("pricing", "报价发我一份"),
    ("product", "有没有企业版"),
    ("product", "介绍一下产品功能"),
    ("help", "登录不了怎么办"),
    ("help", "报错了帮我看一下"),
    ("complaint", "客服一直不回太差了"),
    ("complaint", "我要退款"),
    ("other", "好的谢谢"),
    ("other", "今天天气真好"),
]

GROUP_IDS = [f"group_{i:03d}" for i in range(50)]
USER_IDS = [f"user_{i:04d}" for i in range(100)]

_malformed_variants = [
    {},
    {"message_id": "", "group_id": "g1", "user_id": "u1", "text": "hello"},
    {"message_id": "m1", "group_id": "", "user_id": "u1", "text": "hello"},
    {"message_id": "m1", "group_id": "g1", "user_id": "", "text": "hello"},
    {"message_id": "m1", "group_id": "g1", "user_id": "u1", "text": ""},
    {"message_id": "m1", "group_id": "g1", "user_id": "u1", "text": "   "},
]


@app.post("/demo/sample")
async def demo_sample() -> dict[str, Any]:
    intent_name, text = random.choice(SAMPLE_TEXTS)
    msg = IncomingMessage(
        message_id=f"demo_{random.randint(10000, 99999)}",
        group_id=random.choice(GROUP_IDS[:10]),
        user_id=random.choice(USER_IDS),
        text=text,
    )
    result = await processor.process(msg)
    return result.model_dump()


@app.post("/demo/burst")
async def demo_burst() -> BatchResult:
    """100 messages across 10 groups."""
    messages = []
    for i in range(100):
        intent_name, text = random.choice(SAMPLE_TEXTS)
        messages.append(
            IncomingMessage(
                message_id=f"burst_{i}_{random.randint(1000, 9999)}",
                group_id=random.choice(GROUP_IDS[:10]),
                user_id=random.choice(USER_IDS),
                text=text,
            )
        )
    t0 = time.monotonic()
    results = await processor.process_batch(messages)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return _aggregate(results, elapsed_ms)


@app.post("/demo/load")
async def demo_load() -> BatchResult:
    """1000+ messages across 50 groups — summary only."""
    count = 1200
    messages = []
    for i in range(count):
        intent_name, text = random.choice(SAMPLE_TEXTS)
        messages.append(
            IncomingMessage(
                message_id=f"load_{i}_{random.randint(1000, 9999)}",
                group_id=random.choice(GROUP_IDS),
                user_id=random.choice(USER_IDS),
                text=text,
            )
        )
    t0 = time.monotonic()
    results = await processor.process_batch(messages)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return _aggregate(results, elapsed_ms)


@app.post("/demo/reset")
async def demo_reset() -> dict[str, str]:
    store.reset_all()
    processor.reset()
    return {"status": "reset", "message": "All demo state cleared"}


@app.post("/demo/redis-down")
async def demo_redis_down() -> dict[str, str]:
    store.force_degraded()
    return {"redis": "degraded", "message": "Redis forced to degraded mode"}


@app.post("/demo/redis-recover")
async def demo_redis_recover() -> dict[str, str]:
    recovered = await store.try_recover()
    status = "healthy" if recovered else "degraded"
    return {"redis": status, "message": f"Redis recovery {'succeeded' if recovered else 'failed'}"}


@app.post("/demo/malformed")
async def demo_malformed() -> dict[str, Any]:
    payload = random.choice(_malformed_variants)
    try:
        msg = IncomingMessage(**payload)
    except ValidationError as exc:
        store.invalid_count += 1
        return {"status": "invalid", "errors": json.loads(exc.json()), "payload": payload}

    result = await processor.process(msg)
    return result.model_dump()
