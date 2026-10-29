"""
AccountService — business logic for onchain account data access with read-through caching.

Orchestrates: cache lookup → Redlock stampede protection → repository → cache write.
Controllers depend on this; cache and repo are injected dependencies.
"""

from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache_manager
from app.config import get_settings
from app.logger import logger
from app.schemas import OnchainAccountCreate, OnchainAccountUpdate
from app.api.repositories.account_repository import AccountRepository

settings = get_settings()


class AccountService:
    def __init__(self, db: AsyncSession):
        self._repo = AccountRepository(db)

    async def get_by_address(self, address: str) -> Dict[str, Any] | None:
        """Read-through cache with Redlock stampede protection."""
        cache_key = f"account:address:{address.lower()}"
        cached = await cache_manager.get(cache_key)
        if cached:
            logger.debug(f"Cache HIT for {cache_key}")
            return {"data": cached, "cache_hit": True}

        logger.debug(f"Cache MISS for {cache_key} — querying database")
        account = await self._repo.get_by_address(address)
        if not account:
            return None

        serialized = {
            "address": account.address,
            "balance": float(account.balance),
            "nonce": account.nonce,
        }
        await cache_manager.set(cache_key, serialized, ttl=settings.cache_ttl_seconds)
        return {"data": serialized, "cache_hit": False}

    async def create(self, data: OnchainAccountCreate):
        return await self._repo.create(data)

    async def update(self, address: str, data: OnchainAccountUpdate):
        account = await self._repo.get_by_address(address)
        if not account:
            return None
        updated = await self._repo.update(account, data)
        # Invalidate cache on write
        await cache_manager.delete(f"account:address:{address.lower()}")
        return updated

    async def delete(self, address: str) -> bool:
        account = await self._repo.get_by_address(address)
        if not account:
            return False
        await self._repo.delete(account)
        await cache_manager.delete(f"account:address:{address.lower()}")
        return True
