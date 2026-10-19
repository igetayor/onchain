import asyncio
import json
import time
import uuid
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar, Union
import redis.asyncio as aioredis
from app.config import get_settings
from app.logger import logger

settings = get_settings()

T = TypeVar("T")


class InMemoryCacheStore:
    """Fallback high-speed in-memory cache and locking engine when Redis is unreachable."""

    def __init__(self):
        self._store: Dict[str, Tuple[str, float]] = {}  # key -> (json_value, expire_timestamp)
        self._locks: Dict[str, Tuple[str, float]] = {}  # lock_name -> (identifier, expire_timestamp)
        self._async_locks: Dict[str, asyncio.Lock] = {}

    def _cleanup_expired(self, key: str) -> None:
        if key in self._store:
            _, exp = self._store[key]
            if exp > 0 and time.time() > exp:
                del self._store[key]

    async def get(self, key: str) -> Optional[str]:
        self._cleanup_expired(key)
        if key in self._store:
            val, _ = self._store[key]
            return val
        return None

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        exp = time.time() + ex if ex else 0.0
        self._store[key] = (value, exp)
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count

    async def ttl(self, key: str) -> int:
        self._cleanup_expired(key)
        if key in self._store:
            _, exp = self._store[key]
            if exp == 0.0:
                return -1
            return max(0, int(exp - time.time()))
        return -2

    async def acquire_lock(self, lock_name: str, identifier: str, timeout_ms: int = 5000) -> bool:
        now = time.time()
        if lock_name in self._locks:
            _, exp = self._locks[lock_name]
            if now < exp:
                return False  # Still held
        self._locks[lock_name] = (identifier, now + (timeout_ms / 1000.0))
        return True

    async def release_lock(self, lock_name: str, identifier: str) -> bool:
        if lock_name in self._locks:
            held_id, _ = self._locks[lock_name]
            if held_id == identifier:
                del self._locks[lock_name]
                return True
        return False

    async def ping(self) -> bool:
        return True


class DistributedLock:
    """
    Asynchronous Distributed Lock implementing the Redlock algorithm principle.
    Prevents cache stampedes and race conditions across multiple worker nodes.
    """

    # Lua script to atomically release lock only if the token matches
    RELEASE_LUA = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(
        self,
        redis_client: Optional[aioredis.Redis],
        fallback_store: InMemoryCacheStore,
        lock_name: str,
        timeout_ms: int = 5000,
    ):
        self.redis = redis_client
        self.fallback = fallback_store
        self.lock_name = f"lock:{lock_name}"
        self.timeout_ms = timeout_ms
        self.identifier = f"token_{uuid.uuid4().hex}"
        self.acquired = False

    async def __aenter__(self) -> "DistributedLock":
        acquired = await self.acquire()
        if not acquired:
            raise TimeoutError(f"Could not acquire distributed lock for {self.lock_name}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.release()

    async def acquire(self) -> bool:
        if self.redis is not None:
            try:
                # SET key val NX PX timeout_ms
                res = await self.redis.set(
                    self.lock_name,
                    self.identifier,
                    nx=True,
                    px=self.timeout_ms,
                )
                self.acquired = bool(res)
                return self.acquired
            except Exception as exc:
                logger.warning(f"Redis lock failed, falling back: {exc}")

        self.acquired = await self.fallback.acquire_lock(
            self.lock_name, self.identifier, self.timeout_ms
        )
        return self.acquired

    async def release(self) -> bool:
        if not self.acquired:
            return False

        if self.redis is not None:
            try:
                res = await self.redis.eval(
                    self.RELEASE_LUA,
                    1,
                    self.lock_name,
                    self.identifier,
                )
                return bool(res)
            except Exception as exc:
                logger.warning(f"Redis release lock failed: {exc}")

        return await self.fallback.release_lock(self.lock_name, self.identifier)


class CacheManager:
    """
    Enterprise Read-Through and Write-Through Caching Proxy.
    """

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._fallback = InMemoryCacheStore()
        self._metrics = {
            "reads": 0,
            "hits": 0,
            "misses": 0,
            "writes": 0,
            "locks_acquired": 0,
            "stampedes_prevented": 0,
        }

    async def connect(self) -> None:
        """Establishes Redis connection pool if available."""
        try:
            client = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            )
            await client.ping()
            self._redis = client
            logger.info("Connected to Redis caching backend.")
        except Exception as exc:
            logger.warning(f"Redis not reachable ({exc}). Operating with high-speed in-memory DAL cache.")
            self._redis = None

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None

    def lock(self, lock_key: str, timeout_ms: Optional[int] = None) -> DistributedLock:
        return DistributedLock(
            redis_client=self._redis,
            fallback_store=self._fallback,
            lock_name=lock_key,
            timeout_ms=timeout_ms or settings.cache_stampede_lock_timeout_ms,
        )

    async def get_raw(self, key: str) -> Tuple[Optional[str], int]:
        """Returns raw cached string and TTL remaining."""
        if self._redis:
            try:
                val = await self._redis.get(key)
                ttl = await self._redis.ttl(key) if val else -2
                return val, ttl
            except Exception:
                pass
        val = await self._fallback.get(key)
        ttl = await self._fallback.ttl(key) if val else -2
        return val, ttl

    async def set_raw(self, key: str, value: str, ttl_seconds: int) -> None:
        """Stores string payload into cache with expiration."""
        if self._redis:
            try:
                await self._redis.set(key, value, ex=ttl_seconds)
                return
            except Exception:
                pass
        await self._fallback.set(key, value, ex=ttl_seconds)

    async def delete(self, *keys: str) -> int:
        """Invalidates specified keys."""
        count = 0
        if self._redis:
            try:
                count = await self._redis.delete(*keys)
                return count
            except Exception:
                pass
        return await self._fallback.delete(*keys)

    # -------------------------------------------------------------------------
    # Core Read-Through Pattern with Redlock Stampede Protection
    # -------------------------------------------------------------------------
    async def read_through(
        self,
        cache_key: str,
        query_fn: Callable[[], Any],
        ttl_seconds: Optional[int] = None,
    ) -> Tuple[Any, Dict[str, Any]]:
        """
        Executes a resilient read-through cache lookup:
        1. Checks Redis cache. If HIT -> returns immediately without querying database.
        2. If MISS -> acquires Redlock distributed lock to ensure ONLY ONE worker executes expensive query.
        3. Other concurrent requests spin-wait for lock or cached value, preventing cache stampedes.
        4. Populates cache (TTL) and returns result.
        """
        self._metrics["reads"] += 1
        ttl = ttl_seconds or settings.cache_default_ttl_seconds
        start_time = time.perf_counter()

        # Step 1: Check cache
        cached_str, ttl_rem = await self.get_raw(cache_key)
        if cached_str is not None:
            self._metrics["hits"] += 1
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            meta = {
                "cache_status": "HIT",
                "cache_key": cache_key,
                "ttl_remaining_seconds": max(0, ttl_rem),
                "lock_acquired": False,
                "data_source": "CACHE (Redis)",
                "execution_time_ms": duration_ms,
            }
            return json.loads(cached_str), meta

        # Step 2: Cache Miss -> Stampede Guard with Distributed Lock
        self._metrics["misses"] += 1
        lock = self.lock(f"stampede:{cache_key}")

        acquired = await lock.acquire()
        if not acquired:
            # Another concurrent request is actively populating the cache!
            self._metrics["stampedes_prevented"] += 1
            for _ in range(settings.cache_stampede_max_retries):
                await asyncio.sleep(settings.cache_stampede_retry_delay_ms / 1000.0)
                cached_str, ttl_rem = await self.get_raw(cache_key)
                if cached_str is not None:
                    self._metrics["hits"] += 1
                    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                    meta = {
                        "cache_status": "HIT (POST_LOCK)",
                        "cache_key": cache_key,
                        "ttl_remaining_seconds": max(0, ttl_rem),
                        "lock_acquired": False,
                        "data_source": "CACHE (Redis)",
                        "execution_time_ms": duration_ms,
                    }
                    return json.loads(cached_str), meta

            # Fallback if lock holder timed out without writing:
            acquired = await lock.acquire()

        try:
            self._metrics["locks_acquired"] += 1
            # Double check cache inside critical section
            cached_str, ttl_rem = await self.get_raw(cache_key)
            if cached_str is not None:
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return json.loads(cached_str), {
                    "cache_status": "HIT (DOUBLE_CHECK)",
                    "cache_key": cache_key,
                    "ttl_remaining_seconds": ttl_rem,
                    "lock_acquired": True,
                    "data_source": "CACHE (Redis)",
                    "execution_time_ms": duration_ms,
                }

            # Step 3: Execute expensive database query
            db_data = await query_fn()

            # Step 4: Populate cache
            await self.set_raw(cache_key, json.dumps(db_data), ttl)

            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            meta = {
                "cache_status": "MISS",
                "cache_key": cache_key,
                "ttl_remaining_seconds": ttl,
                "lock_acquired": True,
                "data_source": "DATABASE (SQLAlchemy/PostgreSQL)",
                "execution_time_ms": duration_ms,
            }
            return db_data, meta
        finally:
            await lock.release()

    # -------------------------------------------------------------------------
    # Core Write-Through Pattern with Distributed Lock
    # -------------------------------------------------------------------------
    async def write_through(
        self,
        cache_key: str,
        write_fn: Callable[[], Any],
        ttl_seconds: Optional[int] = None,
    ) -> Tuple[Any, Dict[str, Any]]:
        """
        Executes an atomic write-through update:
        1. Acquires distributed lock for target entity.
        2. Executes database mutation within transaction.
        3. Updates or invalidates cache entry atomically.
        4. Releases lock.
        """
        self._metrics["writes"] += 1
        ttl = ttl_seconds or settings.cache_default_ttl_seconds
        start_time = time.perf_counter()

        lock = self.lock(f"write:{cache_key}")
        async with lock:
            self._metrics["locks_acquired"] += 1
            # Persist to database
            result = await write_fn()

            # Synchronize cache immediately (write-through)
            if result is not None:
                await self.set_raw(cache_key, json.dumps(result), ttl)
            else:
                await self.delete(cache_key)

            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            meta = {
                "cache_status": "WRITE_THROUGH",
                "cache_key": cache_key,
                "ttl_remaining_seconds": ttl,
                "lock_acquired": True,
                "data_source": "DATABASE (SQLAlchemy)",
                "execution_time_ms": duration_ms,
            }
            return result, meta

    def get_telemetry(self) -> Dict[str, Any]:
        """Calculates cache hit ratio and metrics."""
        total_reads = self._metrics["reads"]
        hits = self._metrics["hits"]
        ratio = round((hits / total_reads * 100.0), 2) if total_reads > 0 else 0.0
        return {
            "total_reads": total_reads,
            "cache_hits": hits,
            "cache_misses": self._metrics["misses"],
            "cache_hit_ratio_pct": ratio,
            "total_writes": self._metrics["writes"],
            "locks_acquired": self._metrics["locks_acquired"],
            "stampedes_prevented": self._metrics["stampedes_prevented"],
        }


# Singleton cache proxy instance
cache_manager = CacheManager()
