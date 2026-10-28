# Senior Engineering Walkthrough: Onchain Caching Proxy & DAL

> **Target Audience:** Principal Systems Engineers, Database Architects, High-Throughput Web Scale Leads.  
> **Key Architecture Highlights:** Read-Through & Write-Through Caching, Redlock Distributed Stampede Protection, SQLAlchemy 2.0 AsyncIO + PostgreSQL/SQLite, Real-Time Cache Hit/Miss Telemetry Headers.

---

## 1. Executive Summary & Problem Space

High-volume web applications face severe database degradation under the **Cache Stampede (Thundering Herd)** effect. When a popular cached key expires, hundreds or thousands of simultaneous concurrent requests experience a cache miss at the exact same millisecond. All of them bypass the cache and hit the primary database with identical queries, overwhelming connection pools and driving CPU utilization to 100%.

**Onchain** implements a resilient Data Access Layer (DAL) and caching proxy:
- **Transparent Read-Through Caching:** Intercepts requests, checking Redis before hitting the database.
- **Atomic Write-Through Invalidation:** Writes mutate the database and update or evict the cache atomically.
- **Redlock Distributed Spin-Lock:** When a cache miss occurs under high concurrency, only a single worker acquires the distributed lock to query PostgreSQL; all other requests await the cached result on a non-blocking spin-lock.
- **Observability Headers:** Every response provides `X-Cache-Status` (`HIT`, `MISS`, `WRITE_THROUGH`), `X-Data-Source`, and `X-Process-Time-Ms`.

---

## 2. File & Function Code Architecture

### `app/main.py` — Gateway & Routing (Port 8085)
- Bootstraps FastAPI, registers middleware injecting correlation IDs and latency timers, and mounts the interactive caching control room at `GET /`.

### `app/services/cache_service.py` — Caching Proxy Engine
- `get_or_set(key, fetch_fn, ttl)`: Checks Redis. On miss, acquires the Redlock distributed mutex (`SET key NX PX`), queries the database via `fetch_fn`, writes the result to Redis, and releases the lock.
- `invalidate(key)` / `set_cached(key, value)`: Coordinates write-through cache consistency.

### `app/db/` — Asynchronous Persistence
- Configures SQLAlchemy 2.0 async engine and session factory, supporting PostgreSQL `asyncpg` with fallback to async SQLite for local execution.

---

## 3. How to Run the Live Demo

### Starting the Server (Port 8085)
```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8085
```
Open your browser to: **http://127.0.0.1:8085**

### Demo Walkthrough Script
1. **Show the Dashboard:** Point out the dark-mode telemetry panel tracking Cache Hit Ratio, Redis Latency, and Database Query Time.
2. **First Query (Cache MISS):** Query an account address (e.g. `0x71C...`).
   - Notice the response header: `X-Cache-Status: MISS`.
   - Notice the latency: ~25ms (hitting relational database).
3. **Second Query (Cache HIT):** Query the exact same address again.
   - Notice the response header: `X-Cache-Status: HIT`.
   - Notice the latency drops to: ~0.8ms (served directly from Redis memory).
4. **Demonstrate Stampede Protection:** Run the concurrent load simulation tool in the UI. Show that under 50 simultaneous concurrent requests for an uncached key, the database query count increments by **only 1**, while the other 49 requests are safely served from the freshly primed cache without database contention.
