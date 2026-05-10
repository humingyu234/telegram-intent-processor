"""Redis-backed message store with in-memory fallback."""

import asyncio
from collections import defaultdict

import redis.asyncio as redis

from app.models import GroupSnapshot, ProcessingResult


class MessageStore:
    """Stores processed messages and group state.

    Primary: Redis. Fallback: in-memory dict.
    When Redis is unreachable, the store degrades gracefully and exposes
    a ``degraded`` flag.

    Startup without Redis is NOT degraded — the store just works in
    local-only mode. Degraded means "Redis was connected and then lost".
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
        self.redis_persisted = 0   # written to Redis
        self.fallback_count = 0    # written to memory fallback
        self.invalid_count = 0
        self.duplicate_count = 0

    @property
    def total_processed(self) -> int:
        return self.redis_persisted + self.fallback_count

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
            # No Redis at startup — not an error, just local mode
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

        # Memory fallback — protected by per-message lock
        lock = self._msg_locks[message_id]
        async with lock:
            if message_id in self._messages:
                return False
            self._messages[message_id] = "claimed"
            return True

    async def save_result(self, result: ProcessingResult) -> bool:
        """Persist a processing result. Returns False if fallback was used."""
        key = f"msg:{result.message_id}"
        value = result.model_dump_json()

        try:
            if self._connected and self._redis:
                await self._redis.set(key, value)
                self.redis_persisted += 1
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
        lock = self._group_locks[group_id]

        async with lock:
            try:
                if self._connected and self._redis:
                    await self._redis.set(f"group:{group_id}", value)
                    return True
            except (redis.ConnectionError, redis.TimeoutError, OSError):
                self._mark_degraded()

            self._group_states[group_id] = snapshot.model_dump()
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
        status = "healthy"
        if self.degraded:
            status = "degraded"
        elif not self._connected:
            status = "local"
        return {
            "redis": status,
            "total_processed": self.total_processed,
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
        # Only mark degraded if we actually had a Redis connection.
        # If Redis was never connected (started in local mode), it's
        # a no-op — local mode is normal, not degraded.
        if self._connected:
            self.degraded = True
            self._connected = False
