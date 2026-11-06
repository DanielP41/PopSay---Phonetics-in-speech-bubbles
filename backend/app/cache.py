"""Caché de audio: Redis si hay REDIS_URL, si no un LRU en memoria acotado por tamaño."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Protocol

from .providers import Audio


def audio_key(provider: str, voice: str, accent: str, text: str) -> str:
    raw = json.dumps([provider, voice, accent, text], ensure_ascii=False)
    return "popsay:tts:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AudioCache(Protocol):
    name: str

    async def get(self, key: str) -> Audio | None: ...

    async def set(self, key: str, audio: Audio) -> None: ...

    async def close(self) -> None: ...


class MemoryCache:
    name = "memory"

    def __init__(self, max_bytes: int, ttl_seconds: int):
        self.max_bytes = max_bytes
        self.ttl = ttl_seconds
        self.size = 0
        self.items: OrderedDict[str, tuple[float, Audio]] = OrderedDict()

    async def get(self, key: str) -> Audio | None:
        item = self.items.get(key)
        if not item:
            return None
        expires, audio = item
        if expires < time.monotonic():
            self._drop(key)
            return None
        self.items.move_to_end(key)
        return audio

    async def set(self, key: str, audio: Audio) -> None:
        if len(audio.data) > self.max_bytes:
            return
        if key in self.items:
            self._drop(key)
        self.items[key] = (time.monotonic() + self.ttl, audio)
        self.size += len(audio.data)
        while self.size > self.max_bytes and self.items:
            self._drop(next(iter(self.items)))

    def _drop(self, key: str) -> None:
        _, audio = self.items.pop(key)
        self.size -= len(audio.data)

    async def close(self) -> None:
        self.items.clear()
        self.size = 0


class RedisCache:
    name = "redis"

    def __init__(self, url: str, ttl_seconds: int):
        import redis.asyncio as redis

        self.r = redis.from_url(url)
        self.ttl = ttl_seconds

    async def get(self, key: str) -> Audio | None:
        data = await self.r.hmget(key, "t", "d")
        if not data or data[1] is None:
            return None
        return Audio(data[1], (data[0] or b"audio/mpeg").decode())

    async def set(self, key: str, audio: Audio) -> None:
        async with self.r.pipeline(transaction=True) as p:
            p.hset(key, mapping={"t": audio.media_type, "d": audio.data})
            p.expire(key, self.ttl)
            await p.execute()

    async def close(self) -> None:
        await self.r.aclose()


def build_cache(redis_url: str, ttl_seconds: int, memory_max_mb: int) -> AudioCache:
    if redis_url:
        return RedisCache(redis_url, ttl_seconds)
    return MemoryCache(memory_max_mb * 1024 * 1024, ttl_seconds)
