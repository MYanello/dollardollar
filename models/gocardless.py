from datetime import UTC, timedelta
from datetime import datetime as dt
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, Text, case, func
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.sql.elements import Case

from .base import Base

if TYPE_CHECKING:
    from models import User


class Agreement(Base):
    """Stores information about GoCardless user agreements."""

    __tablename__ = "GoCardless_agreements"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("users.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String, nullable=False)
    max_historical_days: Mapped[int] = mapped_column()
    access_valid_for_days: Mapped[int] = mapped_column()
    scope_balances: Mapped[bool] = mapped_column()
    scope_details: Mapped[bool] = mapped_column()
    scope_transactions: Mapped[bool] = mapped_column()
    expiry_date: Mapped[dt] = mapped_column(nullable=False)
    requisitions: Mapped[list["Requisition"]] = relationship(
        "Requisition", back_populates="agreement", lazy=True
    )

    created_at: Mapped[dt] = mapped_column(default=dt.now(UTC))

    @hybrid_property
    def expired(self) -> bool:  # type: ignore[reportRedeclaration]
        return (
            self.created_at + timedelta(days=self.access_valid_for_days)
        ) < dt.now(UTC)

    @expired.expression
    @classmethod
    def expired(cls) -> Case[Any]:
        return case(
            (
                cls.expiry_date < func.current_date(),
                True,
            ),
            else_=False,
        )


class Requisition(Base):
    """Stores information about GoCardless requisitions."""

    __tablename__ = "GoCardless_requisitions"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("users.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String, nullable=False)
    agreement_id: Mapped[str] = mapped_column(
        String, ForeignKey("GoCardless_agreements.id"), nullable=False
    )

    user: Mapped["User"] = relationship(
        "User", back_populates="requisitions", lazy=True
    )

    agreement: Mapped["Agreement"] = relationship(
        "Agreement", back_populates="requisitions", lazy=True
    )

    created_at: Mapped[dt] = mapped_column(default=dt.now(UTC))


class GoCardlessSettings(Base):
    """Stores GoCardless connection settings for a user."""

    __tablename__ = "GoCardless"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("users.id"), nullable=False, unique=True
    )
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationship with User
    user: Mapped["User"] = relationship(
        "User", back_populates="gocardless", uselist=False, lazy=True
    )
    last_sync: Mapped[dt | None] = mapped_column(default=None)

    temp_accounts: Mapped[str | None] = mapped_column(Text, default=None)

    access_token_expiration: Mapped[dt] = mapped_column(default=dt.now(UTC))
    refresh_token_expiration: Mapped[dt] = mapped_column(default=dt.now(UTC))
    enabled: Mapped[bool] = mapped_column(default=True)
    sync_frequency: Mapped[str] = mapped_column(
        String(20), default="daily"
    )  # 'daily', 'weekly', etc.
    created_at: Mapped[dt] = mapped_column(default=dt.now(UTC))
    updated_at: Mapped[dt] = mapped_column(
        default=dt.now(UTC),
        onupdate=dt.now(UTC),
    )

    def __repr__(self):
        """Return string representation of the GoCardless settings."""
        return f"<GoCardless settings for user {self.user_id}>"
