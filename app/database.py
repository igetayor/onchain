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
