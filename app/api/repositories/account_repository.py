"""
AccountRepository — database access layer for OnchainAccount and TransactionRecord.

All raw SQL/ORM queries live here. Services call this; no HTTP knowledge here.
"""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OnchainAccount, TransactionRecord
from app.schemas import OnchainAccountCreate, OnchainAccountUpdate, TransactionRecordCreate


class AccountRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_by_address(self, address: str) -> Optional[OnchainAccount]:
        result = await self._db.execute(
            select(OnchainAccount).where(OnchainAccount.address == address.lower())
        )
        return result.scalar_one_or_none()

    async def create(self, data: OnchainAccountCreate) -> OnchainAccount:
        account = OnchainAccount(**data.model_dump())
        self._db.add(account)
        await self._db.commit()
        await self._db.refresh(account)
        return account

    async def update(self, account: OnchainAccount, data: OnchainAccountUpdate) -> OnchainAccount:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(account, field, value)
        await self._db.commit()
        await self._db.refresh(account)
        return account

    async def delete(self, account: OnchainAccount) -> None:
        await self._db.delete(account)
        await self._db.commit()


class TransactionRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, data: TransactionRecordCreate) -> TransactionRecord:
        record = TransactionRecord(**data.model_dump())
        self._db.add(record)
        await self._db.commit()
        await self._db.refresh(record)
        return record

    async def list_by_account(self, account_id: int, limit: int = 50) -> list[TransactionRecord]:
        result = await self._db.execute(
            select(TransactionRecord)
            .where(TransactionRecord.account_id == account_id)
            .limit(limit)
        )
        return list(result.scalars().all())
