"""Postgres and Redis access.

Deliberately plain asyncpg, no ORM. The hot paths here are a COPY of millions
of delivery rows and a handful of indexed lookups; an ORM buys nothing for
either and makes the COPY awkward.
"""
from __future__ import annotations

import asyncpg
import redis.asyncio as aioredis

from .config import settings

_pool: asyncpg.Pool | None = None
_redis: aioredis.Redis | None = None


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=4,
            max_size=32,
            # Statement cache off: pgbouncer in transaction mode (which you will
            # need past a few hundred workers) rejects prepared statements.
            statement_cache_size=0,
        )
    return _pool


async def redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def close() -> None:
    global _pool, _redis
    if _pool is not None:
        await _pool.close()
        _pool = None
    if _redis is not None:
        await _redis.aclose()
        _redis = None


# --------------------------------------------------------------- queues
# Redis Streams, not a list: a stream gives consumer groups, so send workers
# can scale horizontally and a crashed worker's claim is recoverable via XCLAIM
# instead of silently losing the batch.
FANOUT_STREAM = "comms:fanout"
SEND_STREAM = "comms:send"
STATUS_STREAM = "comms:status"
GROUP = "workers"


async def ensure_streams() -> None:
    r = await redis()
    for stream in (FANOUT_STREAM, SEND_STREAM, STATUS_STREAM):
        try:
            await r.xgroup_create(stream, GROUP, id="0", mkstream=True)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise


async def enqueue(stream: str, **fields: str) -> str:
    r = await redis()
    return await r.xadd(stream, {k: str(v) for k, v in fields.items()})
