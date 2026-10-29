"""
AccountController — FastAPI route handlers (controller layer) for the Onchain API.
Decoupled Controller layer wiring HTTP requests to Service & CacheManager layers.
"""

import time
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache_manager
from app.config import get_settings
from app.database import get_db
from app.models import OnchainAccount, TransactionRecord
from app.schemas import (
    CacheInvalidateResponse,
    CachedResponseEnvelope,
    CacheMetadata,
    HealthCheckResponse,
    MetricsResponse,
    OnchainAccountCreate,
    OnchainAccountResponse,
    OnchainAccountUpdate,
    TransactionRecordCreate,
    TransactionRecordResponse,
)

settings = get_settings()
router = APIRouter(prefix="/api/v1", tags=["Data Access Layer"])
start_time = time.time()


# -------------------------------------------------------------------------
# Read-Through Caching Endpoints
# -------------------------------------------------------------------------

@router.get(
    "/accounts/{address}",
    response_model=CachedResponseEnvelope,
    summary="Get account by address with Read-Through caching & Redlock stampede protection",
)
async def get_account_by_address(
    address: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> CachedResponseEnvelope:
    """
    Executes a read-through cache lookup.
    Automatically queries Redis before hitting the database.
    If cache misses, acquires a Redlock distributed lock to prevent stampedes.
    """
    cache_key = f"account:address:{address.lower()}"

    async def _fetch_from_db():
        stmt = select(OnchainAccount).where(OnchainAccount.address == address)
        result = await db.execute(stmt)
        acc = result.scalar_one_or_none()
        if not acc:
            return None
        return acc.to_dict()

    data, meta = await cache_manager.read_through(cache_key, _fetch_from_db)

    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Onchain account with address '{address}' not found",
        )

    # Attach caching telemetry headers
    response.headers["X-Cache-Status"] = meta["cache_status"]
    response.headers["X-Cache-Key"] = meta["cache_key"]
    response.headers["X-Data-Source"] = meta["data_source"]

    return CachedResponseEnvelope(
        data=data,
        cache_meta=CacheMetadata(**meta),
    )


@router.get(
    "/accounts",
    response_model=CachedResponseEnvelope,
    summary="List accounts with query caching",
)
async def list_accounts(
    response: Response,
    network: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> CachedResponseEnvelope:
    cache_key = f"accounts:list:net={network or 'all'}:lim={limit}"

    async def _fetch_list():
        stmt = select(OnchainAccount)
        if network:
            stmt = stmt.where(OnchainAccount.network == network)
        stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        accounts = result.scalars().all()
        return [acc.to_dict() for acc in accounts]

    data, meta = await cache_manager.read_through(cache_key, _fetch_list, ttl_seconds=60)

    response.headers["X-Cache-Status"] = meta["cache_status"]
    response.headers["X-Cache-Key"] = meta["cache_key"]
    response.headers["X-Data-Source"] = meta["data_source"]

    return CachedResponseEnvelope(
        data=data,
        cache_meta=CacheMetadata(**meta),
    )


# -------------------------------------------------------------------------
# Write-Through Caching Endpoints
# -------------------------------------------------------------------------

@router.post(
    "/accounts",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Create account with Write-Through caching",
)
async def create_account(
    payload: OnchainAccountCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Executes a write-through cache creation:
    1. Writes account row to database
    2. Writes populated record directly into Redis
    """
    cache_key = f"account:address:{payload.address.lower()}"

    async def _save_to_db():
        stmt = select(OnchainAccount).where(OnchainAccount.address == payload.address)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.balance = payload.balance
            existing.network = payload.network
            if payload.label is not None:
                existing.label = payload.label
            existing.is_active = payload.is_active
            existing.version += 1
            await db.commit()
            await db.refresh(existing)
            return existing.to_dict()

        acc = OnchainAccount(
            address=payload.address,
            network=payload.network,
            label=payload.label,
            balance=payload.balance,
            is_active=payload.is_active,
        )
        db.add(acc)
        await db.commit()
        await db.refresh(acc)
        return acc.to_dict()

    data, meta = await cache_manager.write_through(cache_key, _save_to_db)

    response.headers["X-Cache-Status"] = meta["cache_status"]
    response.headers["X-Cache-Key"] = meta["cache_key"]
    response.headers["X-Data-Source"] = meta["data_source"]

    # Invalidate collection caches
    await cache_manager.invalidate_pattern("accounts:list:*")

    return {"data": data, "cache_meta": meta}


@router.put(
    "/accounts/{address}",
    response_model=Dict[str, Any],
    summary="Update account with Write-Through cache synchronization",
)
@router.patch(
    "/accounts/{address}",
    response_model=Dict[str, Any],
    summary="Update account with Write-Through cache synchronization",
)
async def update_account(
    address: str,
    payload: OnchainAccountUpdate,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    cache_key = f"account:address:{address.lower()}"

    async def _update_db():
        stmt = select(OnchainAccount).where(OnchainAccount.address == address)
        result = await db.execute(stmt)
        acc = result.scalar_one_or_none()
        if not acc:
            return None

        update_data = payload.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(acc, key, value)

        acc.version += 1
        await db.commit()
        await db.refresh(acc)
        return acc.to_dict()

    data, meta = await cache_manager.write_through(cache_key, _update_db)

    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Onchain account '{address}' not found",
        )

    response.headers["X-Cache-Status"] = meta["cache_status"]
    response.headers["X-Cache-Key"] = meta["cache_key"]
    response.headers["X-Data-Source"] = meta["data_source"]

    await cache_manager.invalidate_pattern("accounts:list:*")
    return {"data": data, "cache_meta": meta}


@router.delete(
    "/accounts/{address}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete account and evict cache entry",
)
async def delete_account(
    address: str,
    db: AsyncSession = Depends(get_db),
):
    cache_key = f"account:address:{address.lower()}"

    stmt = select(OnchainAccount).where(OnchainAccount.address == address)
    result = await db.execute(stmt)
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Onchain account '{address}' not found",
        )

    await db.delete(acc)
    await db.commit()

    # Evict cache entry and lists
    await cache_manager.delete(cache_key)
    await cache_manager.invalidate_pattern("accounts:list:*")
    return None


# -------------------------------------------------------------------------
# Telemetry, Metrics & Invalidation Endpoints
# -------------------------------------------------------------------------

@router.post(
    "/cache/invalidate",
    response_model=CacheInvalidateResponse,
    summary="Explicit cache eviction",
)
async def invalidate_cache(
    pattern: str = Query(default="account:*", description="Key pattern to evict"),
) -> CacheInvalidateResponse:
    cleared = await cache_manager.invalidate_pattern(pattern)
    return CacheInvalidateResponse(
        cleared_keys=cleared,
        pattern=pattern,
        status="success",
    )


@router.post(
    "/transactions",
    response_model=TransactionRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an immutable transaction",
)
async def create_transaction(
    payload: TransactionRecordCreate,
    db: AsyncSession = Depends(get_db),
) -> TransactionRecordResponse:
    tx = TransactionRecord(
        tx_hash=payload.tx_hash,
        from_address=payload.from_address,
        to_address=payload.to_address,
        amount=payload.amount,
        token_symbol=payload.token_symbol,
        gas_used=payload.gas_used,
        status=payload.status,
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)
    return TransactionRecordResponse(**tx.to_dict())


@router.get("/metrics", response_model=MetricsResponse, summary="Live cache proxy efficiency metrics")
async def get_metrics() -> MetricsResponse:
    telemetry = cache_manager.get_telemetry()
    return MetricsResponse(**telemetry)


@router.get("/health", response_model=HealthCheckResponse, summary="Health check probe")
async def health_probe(db: AsyncSession = Depends(get_db)) -> HealthCheckResponse:
    db_status = "HEALTHY"
    try:
        await db.execute(select(1))
    except Exception as e:
        db_status = f"UNHEALTHY ({e})"

    redis_status = "HEALTHY (ONLINE)" if cache_manager._redis else "STANDALONE_READY (IN_MEMORY_DAL)"

    return HealthCheckResponse(
        status="UP" if "UNHEALTHY" not in db_status else "DEGRADED",
        database=db_status,
        redis=redis_status,
        uptime_seconds=round(time.time() - start_time, 2),
        version=settings.app_version,
    )
