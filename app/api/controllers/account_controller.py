"""
AccountController — FastAPI route handlers (controller layer) for the Onchain API.

Thin adapter: parses requests, calls AccountService, returns HTTP responses.
No DB queries, no cache logic, no business decisions here.
"""

import time
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import (
    CachedResponseEnvelope,
    CacheMetadata,
    HealthCheckResponse,
    OnchainAccountCreate,
    OnchainAccountResponse,
    OnchainAccountUpdate,
)
from app.api.services.account_service import AccountService

router = APIRouter(prefix="/api/v1", tags=["Accounts"])
_start_time = time.time()


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
    service = AccountService(db)
    result = await service.get_by_address(address)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    response.headers["X-Cache"] = "HIT" if result["cache_hit"] else "MISS"
    return CachedResponseEnvelope(
        data=result["data"],
        cache_metadata=CacheMetadata(hit=result["cache_hit"]),
    )


@router.post(
    "/accounts",
    response_model=OnchainAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new onchain account record",
)
async def create_account(
    payload: OnchainAccountCreate,
    db: AsyncSession = Depends(get_db),
) -> OnchainAccountResponse:
    service = AccountService(db)
    account = await service.create(payload)
    return account


@router.patch(
    "/accounts/{address}",
    response_model=OnchainAccountResponse,
    summary="Update an onchain account record",
)
async def update_account(
    address: str,
    payload: OnchainAccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> OnchainAccountResponse:
    service = AccountService(db)
    account = await service.update(address, payload)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


@router.delete(
    "/accounts/{address}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an onchain account record and invalidate its cache",
)
async def delete_account(
    address: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    service = AccountService(db)
    deleted = await service.delete(address)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    summary="Service health and uptime check",
)
async def health_check() -> HealthCheckResponse:
    return HealthCheckResponse(
        status="healthy",
        uptime_seconds=round(time.time() - _start_time, 2),
    )
