from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base declarative class with audit helpers."""
    pass


class OnchainAccount(Base):
    """
    Relational model representing a blockchain ledger account.
    """
    __tablename__ = "onchain_accounts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    address = Column(String(66), unique=True, nullable=False, index=True)
    network = Column(String(32), nullable=False, default="ethereum-mainnet", index=True)
    label = Column(String(64), nullable=True)
    balance = Column(Float, nullable=False, default=0.0)
    is_active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=1)  # Optimistic concurrency control

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    transactions = relationship(
        "TransactionRecord",
        back_populates="account",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_account_address_network", "address", "network"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "address": self.address,
            "network": self.network,
            "label": self.label,
            "balance": self.balance,
            "is_active": self.is_active,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TransactionRecord(Base):
    """
    Relational model representing an immutable onchain transaction.
    """
    __tablename__ = "transaction_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tx_hash = Column(String(66), unique=True, nullable=False, index=True)
    from_address = Column(String(66), nullable=False, index=True)
    to_address = Column(String(66), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    token_symbol = Column(String(16), default="ETH", nullable=False)
    gas_used = Column(Integer, default=21000, nullable=False)
    status = Column(String(20), default="confirmed", nullable=False)

    account_id = Column(Integer, ForeignKey("onchain_accounts.id", ondelete="CASCADE"), nullable=True)
    account = relationship("OnchainAccount", back_populates="transactions")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "tx_hash": self.tx_hash,
            "from_address": self.from_address,
            "to_address": self.to_address,
            "amount": self.amount,
            "token_symbol": self.token_symbol,
            "gas_used": self.gas_used,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
