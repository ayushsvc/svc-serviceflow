"""In-memory and Redis-compatible asynchronous Key-Value store with shared connection pooling."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class InMemoryKV:
    """Thread-safe, asyncio-compatible in-memory key-value store with TTL and script emulation."""

    _instance: InMemoryKV | None = None

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._expires: dict[str, float] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> InMemoryKV:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _clean_expired(self, key: str) -> bool:
        if key in self._expires:
            if time.time() > self._expires[key]:
                self._data.pop(key, None)
                self._expires.pop(key, None)
                return True
        return False

    async def get(self, key: str) -> str | None:
        async with self._lock:
            if self._clean_expired(key):
                return None
            return self._data.get(key)

    async def set(self, key: str, value: str) -> bool:
        async with self._lock:
            self._data[key] = str(value)
            self._expires.pop(key, None)
            return True

    async def setex(self, key: str, ttl: int | float, value: str) -> bool:
        async with self._lock:
            self._data[key] = str(value)
            self._expires[key] = time.time() + float(ttl)
            return True

    async def incr(self, key: str) -> int:
        async with self._lock:
            self._clean_expired(key)
            val = int(self._data.get(key, "0")) + 1
            self._data[key] = str(val)
            return val

    async def expire(self, key: str, ttl: int | float) -> bool:
        async with self._lock:
            if self._clean_expired(key) or key not in self._data:
                return False
            self._expires[key] = time.time() + float(ttl)
            return True

    async def delete(self, *keys: str) -> int:
        async with self._lock:
            count = 0
            for k in keys:
                if k in self._data:
                    self._data.pop(k, None)
                    self._expires.pop(k, None)
                    count += 1
            return count

    async def ttl(self, key: str) -> int:
        async with self._lock:
            if self._clean_expired(key) or key not in self._data:
                return -2
            if key in self._expires:
                remaining = int(self._expires[key] - time.time())
                return max(0, remaining)
            return -1

    async def exists(self, key: str) -> int:
        async with self._lock:
            if self._clean_expired(key):
                return 0
            return 1 if key in self._data else 0

    async def eval(self, script: str, key_count: int, *args: Any) -> Any:
        async with self._lock:
            keys = args[:key_count]
            values = args[key_count:]

            # _deactivate_user_script
            if key_count == 3 and "SET" in script and "EX" in script:
                invitation_key, claim_key, epoch_key = keys
                self._data.pop(invitation_key, None)
                self._expires.pop(invitation_key, None)
                self._data.pop(claim_key, None)
                self._expires.pop(claim_key, None)
                self._data[epoch_key] = str(values[0])
                self._expires[epoch_key] = time.time() + float(values[1])
                return 1

            # _claim_invitation_script
            if "SET" in script and "NX" in script:
                invitation_key, claim_key = keys
                token_id = str(values[0])
                ttl = float(values[1])
                self._clean_expired(invitation_key)
                self._clean_expired(claim_key)
                if self._data.get(invitation_key) != token_id or claim_key in self._data:
                    return 0
                self._data[claim_key] = token_id
                self._expires[claim_key] = time.time() + ttl
                return 1

            # _consume_invitation_script
            if key_count == 2 and "TTL" in script:
                invitation_key, claim_key = keys
                token_id = str(values[0])
                self._clean_expired(invitation_key)
                self._clean_expired(claim_key)
                if self._data.get(invitation_key) != token_id or self._data.get(claim_key) != token_id:
                    return 0
                rem_ttl = 86400
                if invitation_key in self._expires:
                    rem_ttl = max(1, int(self._expires[invitation_key] - time.time()))
                self._data.pop(invitation_key, None)
                self._expires.pop(invitation_key, None)
                return rem_ttl

            # _restore_invitation_script
            if key_count == 2 and "EXISTS" in script:
                invitation_key, claim_key = keys
                token_id = str(values[0])
                ttl = float(values[1])
                self._clean_expired(claim_key)
                if self._data.get(claim_key) != token_id:
                    return 0
                self._clean_expired(invitation_key)
                if invitation_key not in self._data:
                    self._data[invitation_key] = token_id
                    self._expires[invitation_key] = time.time() + ttl
                self._data.pop(claim_key, None)
                self._expires.pop(claim_key, None)
                return 1

            # _replace_invitation_script
            if key_count == 2 and "SET" in script and "DEL" in script:
                invitation_key, claim_key = keys
                token_id = str(values[0])
                ttl = float(values[1])
                self._data[invitation_key] = token_id
                self._expires[invitation_key] = time.time() + ttl
                self._data.pop(claim_key, None)
                self._expires.pop(claim_key, None)
                return 1

            # _verify_invitation_claim_script
            if key_count == 2 and "return 1" in script and "KEYS[2]" in script and "DEL" not in script:
                invitation_key, claim_key = keys
                token_id = str(values[0])
                self._clean_expired(invitation_key)
                self._clean_expired(claim_key)
                return int(self._data.get(invitation_key) == token_id and self._data.get(claim_key) == token_id)

            # _release_invitation_claim_script
            if key_count == 1 and "DEL" in script:
                claim_key = keys[0]
                token_id = str(values[0])
                self._clean_expired(claim_key)
                if self._data.get(claim_key) == token_id:
                    self._data.pop(claim_key, None)
                    self._expires.pop(claim_key, None)
                    return 1
                return 0

            return 0

    async def aclose(self) -> None:
        """No-op for in-memory store."""
        pass


_redis_pool: Any = None


def get_kv() -> Any:
    """Factory to get the Key-Value client: in-memory or pooled Redis."""
    settings = get_settings()
    url = getattr(settings, "redis_url", "") or ""
    url = url.strip()

    if not url or url.startswith(("memory://", "none://", "local://")):
        return InMemoryKV.get_instance()

    try:
        from redis.asyncio import ConnectionPool
        from redis.asyncio import Redis as AsyncRedis

        global _redis_pool
        if _redis_pool is None:
            _redis_pool = ConnectionPool.from_url(url, decode_responses=True)
        return AsyncRedis(connection_pool=_redis_pool)
    except Exception as exc:
        logger.warning("Could not connect to Redis at %s (%s). Falling back to InMemoryKV.", url, exc)
        return InMemoryKV.get_instance()


class Redis:
    """Drop-in interface for Redis, directing from_url to get_kv()."""

    @classmethod
    def from_url(cls, *args: Any, **kwargs: Any) -> Any:
        return get_kv()
