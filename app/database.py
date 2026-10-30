from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.config import get_settings
from app.logger import logger
from app.models import Base

settings = get_settings()

# Engine instantiation with connection pooling
engine_kwargs = {
    "echo": settings.debug,
    "future": True,
}

# Only configure pool_size for non-sqlite drivers
if not settings.database_url.startswith("sqlite"):
    engine_kwargs.update({
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_pre_ping": True,
    })

engine: AsyncEngine = create_async_engine(settings.database_url, **engine_kwargs)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Creates database schema if not exists (migrations/startup helper)."""
    async with engine.begin() as conn:
        logger.info("Initializing relational database tables...")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Relational database schema ready.")

    async with async_session_factory() as session:
        from app.models import OnchainAccount
        from sqlalchemy import select
        res = await session.execute(select(OnchainAccount).limit(1))
        if not res.scalar_one_or_none():
            demo_acc = OnchainAccount(
                address="0x71C84183204688257F4502F64f26A0a12F8e6B5A",
                network="ethereum-mainnet",
                label="Enterprise Vault #01",
                balance=452.80,
                is_active=True,
            )
            session.add(demo_acc)
            await session.commit()
            logger.info("Seeded initial default demo onchain account.")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
