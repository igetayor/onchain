from functools import lru_cache
from typing import Any, List
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Enterprise caching proxy and DAL configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core Application
    app_name: str = Field(default="Onchain Caching Proxy & DAL", description="Service name")
    app_version: str = Field(default="1.0.0", description="Service version")
    environment: str = Field(default="development", description="Runtime environment")
    debug: bool = Field(default=False, description="Debug mode")

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_value(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes", "on", "debug")
        return bool(v)

    host: str = Field(default="0.0.0.0", description="HTTP host")
    port: int = Field(default=8085, description="HTTP port")
    cors_origins: List[str] = Field(default=["*"], description="Allowed CORS origins")

    # Database Configuration (PostgreSQL / async fallback SQLite)
    database_url: str = Field(
        default="sqlite+aiosqlite:///./onchain.db",
        description="Async SQLAlchemy database connection URL (e.g. postgresql+asyncpg://... or sqlite+aiosqlite:///...)",
    )
    db_pool_size: int = Field(default=10, description="Database connection pool size")
    db_max_overflow: int = Field(default=20, description="Maximum overflow connections")
    db_pool_timeout: int = Field(default=30, description="Connection acquisition timeout in seconds")

    # Redis Cache & Redlock Configuration
    redis_url: str = Field(
        default="redis://localhost:6379/2",
        description="Redis server URL for caching and distributed locks",
    )
    cache_default_ttl_seconds: int = Field(
        default=300,
        description="Default TTL for cached queries in seconds (5 minutes)",
    )
    cache_stampede_lock_timeout_ms: int = Field(
        default=5000,
        description="Redlock expiration timeout in milliseconds",
    )
    cache_stampede_retry_delay_ms: int = Field(
        default=50,
        description="Retry delay interval while waiting for cache population in milliseconds",
    )
    cache_stampede_max_retries: int = Field(
        default=100,
        description="Max retry attempts for cache stampede spin-lock",
    )

    # Logging
    log_level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")
    log_json_format: bool = Field(default=True, description="Enable structured JSON log format")


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
