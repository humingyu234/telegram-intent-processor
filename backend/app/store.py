"""Redis-backed message store with in-memory fallback."""

import asyncio
from collections import defaultdict

import redis.asyncio as redis

from app.models import GroupSnapshot, ProcessingResult


class MessageStore:
    """Stores processed messages and group state.

    Primary: Redis. Fallback: in-memory dict.

    Three operating modes:
    - **Redis mode** — Redis connected, all writes go to Redis.
    - **Local mode** — no Redis at startup, all writes go to memory.
      NOT degraded. Memory is the primary store here.
    - **Degraded mode** — Redis WAS connected, then failed. Writes go
      to memory and are counted as fallback writes.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379") -> None:
        self._redis_url = redis_url
        self._redis: redis.Redis | None = None
        self._connected = False

        # In-memory fallback
        self._messages: dict[str, str] = {}
        self._group_states: dict[str, dict] = {}

        # Per-group locks for fallback store consistency
        self._group_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Per-message locks for atomic dedup in fallback mode
        self._msg_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Counters
        self.degraded = False
        self.total_stored = 0     # all successful message writes
        self.redis_persisted = 0  # subset: written to Redis
        self.fallback_count = 0   # subset: written to memory while degraded
        self.invalid_count = 0
        self.duplicate_count = 0

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            self._redis = redis.from_url(
                self._redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                retry_on_timeout=False,
            )
            await self._redis.ping()
            self._connected = True
            self.degraded = False
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            self._connected = False
            self.degraded = False

    async def disconnect(self) -> None:
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
        self._connected = False

    # ------------------------------------------------------------------
    # Atomic message claim (dedup + reserve in one step)
    # ------------------------------------------------------------------

    async def try_claim_message(self, message_id: str) -> bool:
        """Atomically claim a message_id.

        Returns True if this is the first claim (proceed with processing).
        Returns False if already claimed (duplicate).

        Redis path: SET key value NX — atomic check-and-set.
        Memory path: per-message-id lock guarding the check + insert.
        """
        try:
            if self._connected and self._redis:
                result = await self._redis.set(
                    f"msg:{message_id}", "claimed", nx=True
                )
                return result is not None
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            self._mark_degraded()

        lock = self._msg_locks[message_id]
        async with lock:
            if message_id in self._messages:
                return False
            self._messages[message_id] = "claimed"
            return True

    # ------------------------------------------------------------------
    # Save message result
    # ------------------------------------------------------------------

    async def save_result(self, result: ProcessingResult) -> None:
        """Persist a processing result.

        In Redis mode: writes to Redis, increments redis_persisted.
        In local mode: writes to memory (not a fallback).
        In degraded mode: writes to memory, increments fallback_count.
        """
        key = f"msg:{result.message_id}"
        value = result.model_dump_json()

        try:
            if self._connected and self._redis:
                await self._redis.set(key, value)
                self.total_stored += 1
                self.redis_persisted += 1
                return
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            self._mark_degraded()

        self._messages[result.message_id] = value
        self.total_stored += 1
        if self.degraded:
            self.fallback_count += 1

    # ------------------------------------------------------------------
    # Group state
    # ------------------------------------------------------------------

    async def save_group_state(
        self, group_id: str, snapshot: GroupSnapshot
    ) -> None:
        """Persist group state snapshot."""
        value = snapshot.model_dump_json()
        lock = self._group_locks[group_id]

        async with lock:
            try:
                if self._connected and self._redis:
                    await self._redis.set(f"group:{group_id}", value)
                    return
            except (redis.ConnectionError, redis.TimeoutError, OSError):
                self._mark_degraded()

            self._group_states[group_id] = snapshot.model_dump()

    async def get_group_state(
        self, group_id: str
    ) -> GroupSnapshot | None:
        """Load persisted group state, or None if not found."""
        try:
            if self._connected and self._redis:
                raw = await self._redis.get(f"group:{group_id}")
                if raw:
                    return GroupSnapshot.model_validate_json(raw)
                return None
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            self._mark_degraded()

        data = self._group_states.get(group_id)
        if data:
            return GroupSnapshot(**data)
        return None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health(self) -> dict:
        status = "healthy" if self._connected else "local"
        if self.degraded:
            status = "degraded"
        return {
            "redis": status,
            "total_processed": self.total_stored,
            "redis_persisted": self.redis_persisted,
            "fallback_writes": self.fallback_count,
            "invalid_messages": self.invalid_count,
            "duplicate_messages": self.duplicate_count,
        }

    # ------------------------------------------------------------------
    # Force degrade / recover / reset (for demo)
    # ------------------------------------------------------------------

    def reset_all(self) -> None:
        """Clear all in-memory state and reset counters."""
        self._messages.clear()
        self._group_states.clear()
        self._msg_locks.clear()
        self.total_stored = 0
        self.redis_persisted = 0
        self.fallback_count = 0
        self.invalid_count = 0
        self.duplicate_count = 0
        self.degraded = False

    def force_degraded(self) -> None:
        self._redis = None
        self._connected = False
        self.degraded = True

    async def try_recover(self) -> bool:
        await self.connect()
        return not self.degraded

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _mark_degraded(self) -> None:
        if self._connected:
            self.degraded = True
            self._connected = False
