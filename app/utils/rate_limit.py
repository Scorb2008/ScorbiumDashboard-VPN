import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base
from app.utils.log import log


class RateLimitRecord(Base):
    __tablename__ = "rate_limit_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String(64), nullable=False, index=True)
    endpoint = Column(String(128), nullable=False)
    count = Column(Integer, default=1, nullable=False)
    window_start = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class RateLimiter:
    """Database-backed rate limiter for multi-worker environments.

    For production, Redis is recommended for better performance.
    Set REDIS_URL in environment to use Redis-backed rate limiting.
    """

    def __init__(
        self,
        session: AsyncSession,
        max_requests: int = 60,
        window_seconds: int = 60,
    ) -> None:
        self.session = session
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def is_rate_limited(self, ip: str, endpoint: str) -> bool:
        """Check if IP is rate limited for given endpoint."""
        now = datetime.now(timezone.utc)
        window_start = now.replace(second=0, microsecond=0)

        result = await self.session.execute(
            select(RateLimitRecord).where(
                RateLimitRecord.ip == ip,
                RateLimitRecord.endpoint == endpoint,
                RateLimitRecord.window_start >= window_start,
            )
        )
        record = result.scalar_one_or_none()

        if record and record.count >= self.max_requests:
            log.warning(f"Rate limited: {ip} for {endpoint}")
            return True

        return False

    async def record_request(self, ip: str, endpoint: str) -> None:
        """Record a request for rate limiting."""
        now = datetime.now(timezone.utc)
        window_start = now.replace(second=0, microsecond=0)

        result = await self.session.execute(
            select(RateLimitRecord).where(
                RateLimitRecord.ip == ip,
                RateLimitRecord.endpoint == endpoint,
                RateLimitRecord.window_start >= window_start,
            )
        )
        record = result.scalar_one_or_none()

        if record:
            record.count += 1
        else:
            record = RateLimitRecord(
                ip=ip,
                endpoint=endpoint,
                count=1,
                window_start=window_start,
            )
            self.session.add(record)

    async def cleanup_old_records(self) -> int:
        """Clean up old records older than 1 hour."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        result = await self.session.execute(
            select(func.count())
            .select_from(RateLimitRecord)
            .where(RateLimitRecord.created_at < cutoff)
        )
        count = result.scalar_one()

        if count > 0:
            await self.session.execute(
                RateLimitRecord.__table__.delete().where(
                    RateLimitRecord.created_at < cutoff
                )
            )
            log.info(f"Cleaned up {count} rate limit records")

        return count


_redis_client = None
_redis_retry_after = 0.0


async def disable_redis_client(reason: str) -> None:
    """Drop the cached Redis client and back off before the next retry."""
    global _redis_client, _redis_retry_after

    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception:
            pass

    _redis_client = None
    _redis_retry_after = time.monotonic() + 60
    log.warning(f"Redis rate limit backend disabled: {reason}")


async def get_redis_client():
    """Get Redis client if available."""
    global _redis_client, _redis_retry_after

    if _redis_client is None:
        if time.monotonic() < _redis_retry_after:
            return None
        try:
            import redis.asyncio as redis
            from app.core.config import config

            redis_url = config.utils.redis_url
            if redis_url:
                client = redis.from_url(
                    redis_url,
                    socket_connect_timeout=1,
                    socket_timeout=1,
                )
                await client.ping()
                _redis_client = client
                _redis_retry_after = 0.0
                log.info("Redis rate limit backend enabled")
        except ImportError:
            log.warning("redis package is not installed; using non-Redis rate limiting")
        except Exception as e:
            await disable_redis_client(str(e))
    return _redis_client


class RedisRateLimiter:
    """Redis-backed rate limiter for production."""

    def __init__(
        self,
        key_prefix: str = "rate_limit",
        max_requests: int = 60,
        window_seconds: int = 60,
    ) -> None:
        self.key_prefix = key_prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def _get_key(self, ip: str, endpoint: str) -> str:
        return f"{self.key_prefix}:{endpoint}:{ip}"

    async def is_rate_limited(self, ip: str, endpoint: str) -> bool:
        redis = await get_redis_client()
        if not redis:
            return False

        key = self._get_key(ip, endpoint)
        count = await redis.get(key)

        if count and int(count) >= self.max_requests:
            log.warning(f"Rate limited (Redis): {ip} for {endpoint}")
            return True

        return False

    async def record_request(self, ip: str, endpoint: str) -> None:
        redis = await get_redis_client()
        if not redis:
            return

        key = self._get_key(ip, endpoint)
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, self.window_seconds)
        await pipe.execute()
