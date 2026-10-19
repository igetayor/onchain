import asyncio
import pytest
from app.cache import cache_manager


@pytest.mark.asyncio
async def test_cache_set_and_get():
    await cache_manager.set_raw("test:k1", '{"value": 42}', ttl_seconds=60)
    val, ttl = await cache_manager.get_raw("test:k1")
    assert val == '{"value": 42}'
    assert ttl > 0
    await cache_manager.delete("test:k1")
    val_after, _ = await cache_manager.get_raw("test:k1")
    assert val_after is None


@pytest.mark.asyncio
async def test_read_through_caching_behavior():
    query_executions = 0

    async def expensive_query():
        nonlocal query_executions
        query_executions += 1
        return {"data": "expensive_payload", "exec_count": query_executions}

    # First call: cache miss -> queries DB
    res1, meta1 = await cache_manager.read_through("test:rt_key", expensive_query, ttl_seconds=60)
    assert meta1["cache_status"] == "MISS"
    assert res1["exec_count"] == 1
    assert query_executions == 1

    # Second call: cache hit -> serves from cache without hitting DB
    res2, meta2 = await cache_manager.read_through("test:rt_key", expensive_query, ttl_seconds=60)
    assert "HIT" in meta2["cache_status"]
    assert res2["exec_count"] == 1
    assert query_executions == 1  # Unchanged!


@pytest.mark.asyncio
async def test_redlock_mutual_exclusion():
    lock1 = cache_manager.lock("exclusive_resource", timeout_ms=2000)
    lock2 = cache_manager.lock("exclusive_resource", timeout_ms=2000)

    acq1 = await lock1.acquire()
    assert acq1 is True

    # Lock2 cannot acquire while lock1 is held
    acq2 = await lock2.acquire()
    assert acq2 is False

    # Once lock1 released, lock2 can acquire
    await lock1.release()
    acq2_retry = await lock2.acquire()
    assert acq2_retry is True
    await lock2.release()


@pytest.mark.asyncio
async def test_cache_stampede_prevention():
    query_runs = 0

    async def simulated_heavy_query():
        nonlocal query_runs
        await asyncio.sleep(0.05)  # Simulate DB latency
        query_runs += 1
        return {"result": "ok"}

    # Launch 10 concurrent requests at the exact same moment on a cold cache
    tasks = [
        cache_manager.read_through("test:stampede_key", simulated_heavy_query, ttl_seconds=30)
        for _ in range(10)
    ]
    results = await asyncio.gather(*tasks)

    # Expensive query must only have executed ONCE, all others served from cache/lock
    assert query_runs == 1
    assert len(results) == 10
    for res, meta in results:
        assert res == {"result": "ok"}
