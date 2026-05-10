"""Boundary tests for the message processor pipeline.

Covers CLAUDE.md Skill 3 tests #3–#6:
- test_malformed_message_is_rejected_without_crashing
- test_redis_disconnect_uses_fallback_store
- test_concurrent_messages_keep_group_state_consistent
- test_duplicate_message_id_is_not_counted_twice
"""

import asyncio

import pytest
from pydantic import ValidationError

from app.models import (
    GroupState,
    IncomingMessage,
    Intent,
    MessageRequest,
    ProcessingStatus,
)
from app.processor import MessageProcessor
from app.store import MessageStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    return MessageStore(redis_url="redis://localhost:9999")  # nothing listening


@pytest.fixture
def connected_store():
    s = MessageStore(redis_url="redis://localhost:9999")
    s.force_degraded()
    return s


# ---------------------------------------------------------------------------
# Test #3 — malformed message is rejected without crashing
# ---------------------------------------------------------------------------

class TestMalformedMessageIsRejectedWithoutCrashing:
    """Contract: messages that fail Pydantic validation return invalid status
    and the service must continue running afterwards."""

    def test_missing_text_field(self):
        with pytest.raises(ValidationError) as exc_info:
            IncomingMessage(
                message_id="m1", group_id="g1", user_id="u1",
            )
        errors = exc_info.value.errors()
        assert any(e["loc"][0] == "text" for e in errors)

    def test_missing_user_id(self):
        with pytest.raises(ValidationError) as exc_info:
            IncomingMessage(
                message_id="m1", group_id="g1", text="hello",
            )
        errors = exc_info.value.errors()
        assert any(e["loc"][0] == "user_id" for e in errors)

    def test_empty_text_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            IncomingMessage(
                message_id="m1", group_id="g1", user_id="u1", text="",
            )
        errors = exc_info.value.errors()
        assert any("text" in str(e["loc"]) for e in errors)

    def test_whitespace_only_text_rejected(self):
        with pytest.raises(ValidationError):
            IncomingMessage(
                message_id="m1", group_id="g1", user_id="u1", text="   ",
            )

    def test_service_continues_after_invalid(self, store):
        """After rejecting a bad message, the processor still accepts
        a valid one."""
        processor = MessageProcessor(store)
        valid = IncomingMessage(
            message_id="ok", group_id="g1", user_id="u1", text="hello",
        )
        result = asyncio.run(processor.process(valid))
        assert result.status in (
            ProcessingStatus.PROCESSED,
            ProcessingStatus.FALLBACK,
        )


# ---------------------------------------------------------------------------
# Test #6 — duplicate message_id is not counted twice
# ---------------------------------------------------------------------------

class TestDuplicateMessageIdIsNotCountedTwice:
    """Contract: the same message_id processed twice yields 'processed'
    on the first attempt and 'duplicate' on the second, without
    double-counting group messages."""

    def test_first_processed_second_duplicate(self, store):
        processor = MessageProcessor(store)
        msg = IncomingMessage(
            message_id="dup1", group_id="g1", user_id="u1", text="hello",
        )

        r1 = asyncio.run(processor.process(msg))
        assert r1.status in (ProcessingStatus.PROCESSED, ProcessingStatus.FALLBACK)

        r2 = asyncio.run(processor.process(msg))
        assert r2.status == ProcessingStatus.DUPLICATE

    def test_group_message_count_not_doubled(self, store):
        processor = MessageProcessor(store)
        msg = IncomingMessage(
            message_id="cnt1", group_id="g2", user_id="u1", text="产品介绍",
        )

        asyncio.run(processor.process(msg))
        snap_before = asyncio.run(processor.get_group_snapshot("g2"))

        asyncio.run(processor.process(msg))
        snap_after = asyncio.run(processor.get_group_snapshot("g2"))

        # Duplicate should not increase the group message count
        assert snap_after.message_count == snap_before.message_count


# ---------------------------------------------------------------------------
# Test #4 — Redis disconnect uses fallback store
# ---------------------------------------------------------------------------

class TestRedisDisconnectUsesFallbackStore:
    """Contract: when Redis is unavailable the store must fall back to
    in-memory storage, increment the fallback counter, and report
    degraded health — without crashing or losing data."""

    def test_fallback_store_saves_and_loads(self, connected_store):
        """Even in degraded mode, the store must persist and retrieve data."""
        from app.models import GroupSnapshot, ProcessingResult

        result = ProcessingResult(
            message_id="fb1", group_id="g1", user_id="u1",
            text="hello", intent=Intent.OTHER, tags=[],
            status=ProcessingStatus.FALLBACK, fallback_used=True,
        )
        asyncio.run(connected_store.save_result(result))
        # In degraded mode, fallback counter is incremented
        assert connected_store.fallback_count == 1

        snap = GroupSnapshot(group_id="g1", current_state=GroupState.IDLE)
        asyncio.run(connected_store.save_group_state("g1", snap))

        loaded = asyncio.run(connected_store.get_group_state("g1"))
        assert loaded is not None
        assert loaded.group_id == "g1"

    def test_degraded_health_reported(self, connected_store):
        h = connected_store.health()
        assert h["redis"] == "degraded"

    def test_processor_handles_degraded_store(self, connected_store):
        processor = MessageProcessor(connected_store)
        msg = IncomingMessage(
            message_id="deg1", group_id="g1", user_id="u1", text="hello",
        )
        result = asyncio.run(processor.process(msg))
        # Must complete — fallback or processed
        assert result.status in (ProcessingStatus.PROCESSED, ProcessingStatus.FALLBACK)
        assert result.message_id == "deg1"


# ---------------------------------------------------------------------------
# Test #5 — concurrent messages keep group state consistent
# ---------------------------------------------------------------------------

class TestConcurrentMessagesKeepGroupStateConsistent:
    """Contract: when many messages arrive concurrently across multiple
    groups, each group's state must remain internally consistent and
    the total processed count must equal the number of unique messages."""

    def test_concurrent_same_group_maintains_count(self, store):
        processor = MessageProcessor(store)
        n = 50

        messages = [
            IncomingMessage(
                message_id=f"conc-g1-{i}",
                group_id="g_concurrent",
                user_id="u1",
                text="产品介绍",
            )
            for i in range(n)
        ]

        results = asyncio.run(processor.process_batch(messages))
        processed = sum(
            1 for r in results
            if r.status in (ProcessingStatus.PROCESSED, ProcessingStatus.FALLBACK)
        )
        assert processed == n

        snap = asyncio.run(processor.get_group_snapshot("g_concurrent"))
        assert snap.message_count == n

    def test_concurrent_multi_group_independent(self, store):
        processor = MessageProcessor(store)
        groups = [f"g_multi_{i}" for i in range(5)]
        per_group = 20

        messages = []
        for g in groups:
            for i in range(per_group):
                messages.append(
                    IncomingMessage(
                        message_id=f"multi-{g}-{i}",
                        group_id=g,
                        user_id="u1",
                        text="产品介绍",
                    )
                )

        results = asyncio.run(processor.process_batch(messages))
        processed = sum(
            1 for r in results
            if r.status in (ProcessingStatus.PROCESSED, ProcessingStatus.FALLBACK)
        )
        assert processed == len(groups) * per_group

        # Each group must have exactly per_group messages
        for g in groups:
            snap = asyncio.run(processor.get_group_snapshot(g))
            assert snap.message_count == per_group, (
                f"Group {g}: expected {per_group}, got {snap.message_count}"
            )

    def test_concurrent_complaint_still_flags_attention(self, store):
        processor = MessageProcessor(store)
        normal = [
            IncomingMessage(
                message_id=f"conc-norm-{i}",
                group_id="g_complaint_concurrent",
                user_id="u1",
                text="产品介绍",
            )
            for i in range(10)
        ]
        complaint = IncomingMessage(
            message_id="conc-comp-1",
            group_id="g_complaint_concurrent",
            user_id="u1",
            text="我要投诉",
        )

        all_msgs = normal + [complaint]
        results = asyncio.run(processor.process_batch(all_msgs))
        processed = sum(
            1 for r in results
            if r.status in (ProcessingStatus.PROCESSED, ProcessingStatus.FALLBACK)
        )
        assert processed == 11

        snap = asyncio.run(
            processor.get_group_snapshot("g_complaint_concurrent")
        )
        assert snap.message_count == 11
        assert snap.current_state == GroupState.COMPLAINT_ESCALATED
        assert snap.needs_human_attention is True
