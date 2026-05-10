"""Smoke test proving RedisStore can write and read messages and group state.

Requires a running Redis on localhost:6379. Skip gracefully if unavailable.
"""

import asyncio

import pytest
import redis.asyncio as redis

from app.models import GroupSnapshot, GroupState, Intent, ProcessingResult, ProcessingStatus
from app.store import MessageStore


async def _redis_is_available() -> bool:
    try:
        r = redis.from_url("redis://localhost:6379", socket_connect_timeout=1)
        await r.ping()
        await r.aclose()
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_redis_store_write_and_read_message():
    if not await _redis_is_available():
        pytest.skip("Redis not available")

    store = MessageStore(redis_url="redis://localhost:6379")
    await store.connect()
    assert store.degraded is False

    result = ProcessingResult(
        message_id="smt-msg",
        group_id="g1",
        user_id="u1",
        text="hello redis",
        intent=Intent.OTHER,
        tags=[],
        status=ProcessingStatus.PROCESSED,
        fallback_used=False,
    )
    await store.save_result(result)

    # Verify it was written to Redis (not fallback)
    assert store.redis_persisted == 1
    assert store.fallback_count == 0
    assert store.total_stored == 1

    # Claim the same message_id again — should be duplicate
    claimed = await store.try_claim_message("smt-msg")
    assert claimed is False

    await store.disconnect()


@pytest.mark.asyncio
async def test_redis_store_write_and_read_group_state():
    if not await _redis_is_available():
        pytest.skip("Redis not available")

    store = MessageStore(redis_url="redis://localhost:6379")
    await store.connect()

    snap = GroupSnapshot(
        group_id="smt-group",
        current_state=GroupState.PRICING_DISCUSSION,
        message_count=5,
        intent_counts={"pricing": 3, "product": 2},
        needs_human_attention=False,
    )
    await store.save_group_state("smt-group", snap)

    # Read back
    loaded = await store.get_group_state("smt-group")
    assert loaded is not None
    assert loaded.group_id == "smt-group"
    assert loaded.current_state == GroupState.PRICING_DISCUSSION
    assert loaded.message_count == 5
    assert loaded.intent_counts == {"pricing": 3, "product": 2}

    await store.disconnect()


@pytest.mark.asyncio
async def test_redis_degrade_then_fallback():
    """After Redis is forced off, writes should increment fallback_count."""
    if not await _redis_is_available():
        pytest.skip("Redis not available")

    store = MessageStore(redis_url="redis://localhost:6379")
    await store.connect()
    assert store.degraded is False

    # Write while Redis is up
    result = ProcessingResult(
        message_id="degrade-1",
        group_id="g1",
        user_id="u1",
        text="before degrade",
        intent=Intent.OTHER,
        tags=[],
        status=ProcessingStatus.PROCESSED,
        fallback_used=False,
    )
    await store.save_result(result)
    assert store.redis_persisted == 1
    assert store.fallback_count == 0

    # Force degraded
    store.force_degraded()
    assert store.degraded is True

    # Write while degraded
    result2 = ProcessingResult(
        message_id="degrade-2",
        group_id="g1",
        user_id="u1",
        text="after degrade",
        intent=Intent.OTHER,
        tags=[],
        status=ProcessingStatus.PROCESSED,
        fallback_used=True,
    )
    await store.save_result(result2)
    assert store.redis_persisted == 1  # unchanged
    assert store.fallback_count == 1   # new fallback write
    assert store.total_stored == 2     # both messages stored

    await store.disconnect()
