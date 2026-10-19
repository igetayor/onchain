from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssetType(str):
    CRYPTO = "crypto"
    TOKEN = "token"
    NFT = "nft"
    SYNTHETIC = "synthetic"


# -------------------------------------------------------------------------
# Request Schemas
# -------------------------------------------------------------------------

class OnchainAccountCreate(BaseModel):
    """Schema for creating a new ledger account or wallet."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "address": "0x71C84183204688257F4502F64f26A0a12F8e6B5A",
                "network": "ethereum-mainnet",
                "label": "Treasury Cold Wallet",
                "balance": 1425.50,
                "is_active": True,
            }
        },
    )

    address: str = Field(..., min_length=10, max_length=66, description="Cryptographic account address or public key")
    network: str = Field(default="ethereum-mainnet", min_length=3, max_length=32, description="Target blockchain network")
    label: Optional[str] = Field(default=None, max_length=64, description="Human-readable account label")
    balance: float = Field(default=0.0, ge=0.0, description="Native asset balance")
    is_active: bool = Field(default=True, description="Account active status")

    @field_validator("address")
    @classmethod
    def validate_address_format(cls, v: str) -> str:
        if not v.startswith("0x") and not v.isalnum():
            raise ValueError("address must be a valid hex address or alphanumeric public identifier")
        return v


class OnchainAccountUpdate(BaseModel):
    """Schema for updating an existing ledger account."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: Optional[str] = Field(default=None, max_length=64)
    balance: Optional[float] = Field(default=None, ge=0.0)
    is_active: Optional[bool] = Field(default=None)


class TransactionRecordCreate(BaseModel):
    """Schema for logging an immutable onchain transaction record."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tx_hash: str = Field(..., min_length=10, max_length=66, description="Cryptographic transaction hash")
    from_address: str = Field(..., min_length=10, max_length=66)
    to_address: str = Field(..., min_length=10, max_length=66)
    amount: float = Field(..., gt=0.0)
    token_symbol: str = Field(default="ETH", max_length=16)
    gas_used: int = Field(default=21000, ge=0)
    status: str = Field(default="confirmed")


# -------------------------------------------------------------------------
# Response Schemas
# -------------------------------------------------------------------------

class OnchainAccountResponse(BaseModel):
    """Normalized ledger account entity representation."""

    id: int
    address: str
    network: str
    label: Optional[str]
    balance: float
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class TransactionRecordResponse(BaseModel):
    """Normalized transaction record representation."""

    id: int
    tx_hash: str
    from_address: str
    to_address: str
    amount: float
    token_symbol: str
    gas_used: int
    status: str
    created_at: datetime


class CacheMetadata(BaseModel):
    """Information regarding caching proxy execution."""
    cache_status: str = Field(..., description="HIT, MISS, or BYPASS")
    cache_key: str
    ttl_remaining_seconds: Optional[int]
    lock_acquired: bool = False
    data_source: str = Field(..., description="CACHE (Redis) or DATABASE (SQLAlchemy/PostgreSQL)")
    execution_time_ms: float


class CachedResponseEnvelope(BaseModel):
    """Standardized response envelope carrying data and caching proxy telemetry."""
    data: Any
    cache_meta: CacheMetadata


class CacheInvalidateResponse(BaseModel):
    """Result of an explicit cache invalidation command."""
    cleared_keys: int
    pattern: str
    status: str


class HealthCheckResponse(BaseModel):
    """Health check probe response."""
    status: str
    database: str
    redis: str
    uptime_seconds: float
    version: str


class MetricsResponse(BaseModel):
    """Proxy throughput and cache efficiency metrics."""
    total_reads: int
    cache_hits: int
    cache_misses: int
    cache_hit_ratio_pct: float
    total_writes: int
    locks_acquired: int
    stampedes_prevented: int
