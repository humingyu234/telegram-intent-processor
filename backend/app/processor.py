"""Message processing pipeline — the core orchestration layer."""

import asyncio
from collections import defaultdict

from app.classifier import classify_message
from app.models import (
    GroupSnapshot,
    GroupState,
    IncomingMessage,
    Intent,
    ProcessingResult,
    ProcessingStatus,
)
from app.state import GroupStateMachine
from app.store import MessageStore


class MessageProcessor:
    """Orchestrates the full processing pipeline for each incoming message.

    Concurrency model:
    - ``semaphore`` caps the number of in-flight messages globally.
    - Per-group ``asyncio.Lock`` serialises state access within a group,
      while different groups run concurrently.
    - Dedup uses atomic ``try_claim_message`` (SET NX on Redis,
      per-message lock on memory fallback).
    - SSE events are pushed to all connected dashboard subscribers via
      ``event_queues``.
    """

    def __init__(
        self,
        store: MessageStore,
        max_in_flight: int = 50,
    ) -> None:
        self.store = store
        self._max_in_flight = max_in_flight
        self.semaphore = asyncio.Semaphore(max_in_flight)
        self._in_flight = 0
        self._group_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # In-memory group state machines (loaded from store on first access)
        self._state_machines: dict[str, GroupStateMachine] = {}

        # SSE subscriber queues
        self._subscribers: list[asyncio.Queue] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(self, message: IncomingMessage) -> ProcessingResult:
        """Run the full pipeline for one message.

        Pipeline: dedup-claim → semaphore → group-lock → classify →
        state-update → persist → SSE.
        """
        async with self.semaphore:
            self._in_flight += 1
            try:
                return await self._process_impl(message)
            finally:
                self._in_flight -= 1

    async def process_batch(
        self, messages: list[IncomingMessage]
    ) -> list[ProcessingResult]:
        """Process multiple messages concurrently."""
        tasks = [self.process(m) for m in messages]
        return await asyncio.gather(*tasks)

    def reset(self) -> None:
        self._state_machines.clear()
        self._in_flight = 0

    # ------------------------------------------------------------------
    # SSE subscriber management
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Group state helpers
    # ------------------------------------------------------------------

    async def get_group_snapshot(self, group_id: str) -> GroupSnapshot:
        sm = self._state_machines.get(group_id)
        if sm:
            return sm.snapshot()
        return GroupSnapshot(group_id=group_id)

    def all_group_snapshots(self) -> list[dict]:
        return [
            sm.snapshot().model_dump()
            for sm in self._state_machines.values()
        ]

    # ------------------------------------------------------------------
    # Dashboard helpers
    # ------------------------------------------------------------------

    def in_flight_count(self) -> int:
        return self._in_flight

    @property
    def max_in_flight(self) -> int:
        return self._max_in_flight

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    async def _process_impl(
        self, message: IncomingMessage
    ) -> ProcessingResult:
        # — step 1: atomic dedup claim —
        claimed = await self.store.try_claim_message(message.message_id)
        if not claimed:
            self.store.duplicate_count += 1
            return ProcessingResult(
                message_id=message.message_id,
                group_id=message.group_id,
                user_id=message.user_id,
                text=message.text,
                intent=Intent.OTHER,
                tags=[],
                status=ProcessingStatus.DUPLICATE,
                reason="duplicate message_id",
            )

        # — step 2: per-group lock (protects get_or_create → classify → apply → save) —
        lock = self._group_locks[message.group_id]
        async with lock:
            sm = await self._get_or_create_state_machine_locked(
                message.group_id
            )
            group_ctx = sm.snapshot()
            intent, tags = classify_message(
                message.text, group_context=group_ctx
            )
            sm.apply(intent, tags)
            snapshot = sm.snapshot()
            await self.store.save_group_state(
                message.group_id, snapshot
            )

        # — step 3: persist result (outside group lock) —
        is_fallback = self.store.degraded
        result = ProcessingResult(
            message_id=message.message_id,
            group_id=message.group_id,
            user_id=message.user_id,
            text=message.text,
            intent=intent,
            tags=tags,
            status=(
                ProcessingStatus.FALLBACK
                if is_fallback
                else ProcessingStatus.PROCESSED
            ),
            reason=(
                "Redis unavailable, using fallback storage"
                if is_fallback
                else ""
            ),
            group_state_after=snapshot.current_state,
            fallback_used=is_fallback,
        )

        await self.store.save_result(result)
        if is_fallback:
            result = result.model_copy(
                update={
                    "status": ProcessingStatus.FALLBACK,
                    "reason": "Redis unavailable, using fallback storage",
                    "fallback_used": True,
                }
            )

        # — step 4: push SSE —
        await self._broadcast(result, snapshot)

        return result

    async def _get_or_create_state_machine_locked(
        self, group_id: str
    ) -> GroupStateMachine:
        """Load persisted state or create a fresh state machine.

        Must be called while holding the per-group lock for *group_id*.
        """
        if group_id in self._state_machines:
            return self._state_machines[group_id]

        saved = await self.store.get_group_state(group_id)
        if saved:
            sm = GroupStateMachine(
                group_id=group_id,
                current_state=saved.current_state,
                message_count=saved.message_count,
                intent_counts=saved.intent_counts,
                last_intent=saved.last_intent,
                latest_tags=saved.recent_tags,
                needs_human_attention=saved.needs_human_attention,
            )
        else:
            sm = GroupStateMachine(group_id=group_id)

        self._state_machines[group_id] = sm
        return sm

    async def _broadcast(
        self, result: ProcessingResult, snapshot: GroupSnapshot
    ) -> None:
        event = {
            "type": "message_processed",
            "result": result.model_dump(),
            "group_snapshot": snapshot.model_dump(),
            "health": self.store.health(),
            "in_flight": self._in_flight - 1,  # exclude self, not yet decr'd in process()
        }
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)
