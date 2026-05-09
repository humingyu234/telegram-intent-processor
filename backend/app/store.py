"""Redis-backed message store with in-memory fallback."""

import asyncio
from collections import defaultdict

import redis.asyncio as redis

from app.models import GroupSnapshot, GroupState, Intent, ProcessingResult


class MessageStore:
    """Stores processed messages and group state.

    Primary: Redis. Fallback: in-memory dict.
    When Redis is unreachable, the store degrades gracefully and exposes
    a ``degraded`` flag so the rest of the system can surface the status.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379") -> None:
        self._redis_url = redis_url
        self._redis: redis.Redis | None = None
        self._connected = False

        # In-memory fallback
        self._messages: dict[str, str] = {}  # message_id -> status
        self._group_states: dict[str, dict] = {}

        # Per-group locks for fallback store consistency
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # Counters
        self.degraded = False
        self.processed_count = 0
        self.invalid_count = 0
        self.duplicate_count = 0
        self.fallback_count = 0

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
            self.degraded = True

    async def disconnect(self) -> None:
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
        self._connected = False

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    async def check_duplicate(self, message_id: str) -> bool:
        """Return True if this message_id has already been processed."""
        try:
            if self._connected and self._redis:
                return bool(await self._redis.exists(f"msg:{message_id}"))
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            self._mark_degraded()

        return message_id in self._messages

    # ------------------------------------------------------------------
    # Save message result
    # ------------------------------------------------------------------

    async def save_result(self, result: ProcessingResult) -> bool:
        """Persist a processing result. Returns False if fallback was used."""
        key = f"msg:{result.message_id}"
        value = result.model_dump_json()

        try:
            if self._connected and self._redis:
                await self._redis.set(key, value)
                return True
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            self._mark_degraded()

        self._messages[result.message_id] = value
        self.fallback_count += 1
        return False

    # ------------------------------------------------------------------
    # Group state
    # ------------------------------------------------------------------

    async def save_group_state(
        self, group_id: str, snapshot: GroupSnapshot
    ) -> bool:
        """Persist group state snapshot. Returns False if fallback was used."""
        value = snapshot.model_dump_json()
        lock = self._locks[group_id]

        async with lock:
            try:
                if self._connected and self._redis:
                    await self._redis.set(f"group:{group_id}", value)
                    return True
            except (redis.ConnectionError, redis.TimeoutError, OSError):
                self._mark_degraded()

            self._group_states[group_id] = snapshot.model_dump()
            self.fallback_count += 1
            return False

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
        return {
            "redis": "degraded" if self.degraded else "healthy",
            "processed_messages": self.processed_count,
            "invalid_messages": self.invalid_count,
            "duplicate_messages": self.duplicate_count,
            "fallback_writes": self.fallback_count,
        }

    # ------------------------------------------------------------------
    # Force degrade / recover (for demo)
    # ------------------------------------------------------------------

    def force_degraded(self) -> None:
        self._connected = False
        self.degraded = True

    async def try_recover(self) -> bool:
        await self.connect()
        return not self.degraded

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _mark_degraded(self) -> None:
        if not self.degraded:
            self.degraded = True
            self._connected = False
