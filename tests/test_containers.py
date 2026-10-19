import os
import pytest

try:
    from testcontainers.redis import RedisContainer
    HAS_TESTCONTAINERS = True
except ImportError:
    HAS_TESTCONTAINERS = False
    RedisContainer = None

from app.cache import CacheManager
from app.config import Settings


@pytest.mark.skipif(
    not HAS_TESTCONTAINERS or os.getenv("RUN_CONTAINER_TESTS", "0") != "1",
    reason="Docker testcontainers requires 'testcontainers' installed and active Docker engine (RUN_CONTAINER_TESTS=1)",
)
@pytest.mark.asyncio
async def test_containers_redis_real_lock():
    """
    Integration test utilizing testcontainers to spin up isolated real Redis and PostgreSQL containers.
    """
    with RedisContainer("redis:7-alpine") as redis_container:
        redis_host = redis_container.get_container_host_ip()
        redis_port = redis_container.get_exposed_port(6379)
        test_redis_url = f"redis://{redis_host}:{redis_port}/0"

        manager = CacheManager()
        manager._settings = Settings(redis_url=test_redis_url)
        await manager.connect()

        # Verify real Redis distributed lock
        lock = manager.lock("test_container_resource", timeout_ms=3000)
        acq = await lock.acquire()
        assert acq is True
        await lock.release()

        await manager.disconnect()
