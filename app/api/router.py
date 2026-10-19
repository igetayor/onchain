import time
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache_manager
from app.config import get_settings
from app.database import get_db
from app.logger import logger
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
    response_model=CachedResponseEnvelope,
    status_code=status.HTTP_201_CREATED,
    summary="Create account with write-through cache population",
)
async def create_account(
    payload: OnchainAccountCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> CachedResponseEnvelope:
    """
    Creates an account in PostgreSQL/SQLAlchemy and atomically writes to Redis cache.
    """
    cache_key = f"account:address:{payload.address.lower()}"

    async def _create_in_db():
        # Check duplicate
        stmt = select(OnchainAccount).where(OnchainAccount.address == payload.address)
        res = await db.execute(stmt)
        if res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Account {payload.address} already exists",
            )

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

    data, meta = await cache_manager.write_through(cache_key, _create_in_db)

    # Invalidate list cache keys
    await cache_manager.delete("accounts:list:net=all:lim=20")

    response.headers["X-Cache-Status"] = meta["cache_status"]
    response.headers["X-Data-Source"] = meta["data_source"]

    return CachedResponseEnvelope(
        data=data,
        cache_meta=CacheMetadata(**meta),
    )


@router.put(
    "/accounts/{address}",
    response_model=CachedResponseEnvelope,
    summary="Update account with Write-Through atomic lock update",
)
async def update_account(
    address: str,
    payload: OnchainAccountUpdate,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> CachedResponseEnvelope:
    """
    Atomically updates account under Redlock and populates updated cache entry immediately.
    """
    cache_key = f"account:address:{address.lower()}"

    async def _update_in_db():
        stmt = select(OnchainAccount).where(OnchainAccount.address == address)
        res = await db.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Account {address} not found",
            )

        if payload.label is not None:
            acc.label = payload.label
        if payload.balance is not None:
            acc.balance = payload.balance
        if payload.is_active is not None:
            acc.is_active = payload.is_active
        acc.version += 1

        await db.commit()
        await db.refresh(acc)
        return acc.to_dict()

    data, meta = await cache_manager.write_through(cache_key, _update_in_db)

    response.headers["X-Cache-Status"] = meta["cache_status"]
    response.headers["X-Data-Source"] = meta["data_source"]

    return CachedResponseEnvelope(
        data=data,
        cache_meta=CacheMetadata(**meta),
    )


@router.delete(
    "/accounts/{address}",
    response_model=CacheInvalidateResponse,
    summary="Delete account with cache invalidation",
)
async def delete_account(
    address: str,
    db: AsyncSession = Depends(get_db),
) -> CacheInvalidateResponse:
    cache_key = f"account:address:{address.lower()}"

    lock = cache_manager.lock(f"write:{cache_key}")
    async with lock:
        stmt = select(OnchainAccount).where(OnchainAccount.address == address)
        res = await db.execute(stmt)
        acc = res.scalar_one_or_none()
        if not acc:
            raise HTTPException(status_code=404, detail="Account not found")

        await db.delete(acc)
        await db.commit()
        await cache_manager.delete(cache_key)

    return CacheInvalidateResponse(
        cleared_keys=1,
        pattern=cache_key,
        status="INVALIDATED",
    )


# -------------------------------------------------------------------------
# Transactions & Health & Metrics
# -------------------------------------------------------------------------

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
