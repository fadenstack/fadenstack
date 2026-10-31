import uuid
from zoneinfo import ZoneInfo

import pytest
from redis.asyncio import ConnectionPool

from faden_gateway.services.cache.redis import RedisCache
from faden_gateway.services.gateway.lease import LeaseManager
from faden_gateway.services.gateway.ratelimit import Caps, RateLimiter, Reservation


@pytest.mark.anyio
async def test_lease_respects_max_concurrency(fake_redis_pool: ConnectionPool) -> None:
    cache = RedisCache(fake_redis_pool)
    lease = LeaseManager(cache, ttl_sec=30)
    instance_id = uuid.uuid4()
    acquired_first = await lease.try_acquire(
        instance_id=instance_id,
        request_id="req-1",
        max_concurrency=1,
    )
    acquired_second = await lease.try_acquire(
        instance_id=instance_id,
        request_id="req-2",
        max_concurrency=1,
    )
    assert acquired_first is True
    assert acquired_second is False
    await lease.release(instance_id=instance_id, request_id="req-1")
    acquired_third = await lease.try_acquire(
        instance_id=instance_id,
        request_id="req-3",
        max_concurrency=1,
    )
    assert acquired_third is True


@pytest.mark.anyio
async def test_rate_limiter_rpm_and_tpm(fake_redis_pool: ConnectionPool) -> None:
    limiter = RateLimiter(RedisCache(fake_redis_pool))
    utc = ZoneInfo("UTC")
    rpm_1 = await limiter.admit(tenant_id="tenant-x", caps=Caps(rpm=1), tokens=1, zone=utc)
    rpm_2 = await limiter.admit(tenant_id="tenant-x", caps=Caps(rpm=1), tokens=1, zone=utc)
    assert isinstance(rpm_1, Reservation)
    assert not isinstance(rpm_2, Reservation) and rpm_2.kind == "rpm"

    tpm_1 = await limiter.admit(tenant_id="tenant-y", caps=Caps(tpm=5), tokens=3, zone=utc)
    tpm_2 = await limiter.admit(tenant_id="tenant-y", caps=Caps(tpm=5), tokens=3, zone=utc)
    assert isinstance(tpm_1, Reservation)
    assert not isinstance(tpm_2, Reservation) and tpm_2.kind == "tpm"
