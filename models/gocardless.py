from datetime import datetime as dt
from datetime import timedelta
from datetime import timezone as tz
from typing import TYPE_CHECKING, Any, ClassVar, Optional

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

    __tablename__: ClassVar[str] = "GoCardless_agreements"

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

    created_at: Mapped[dt] = mapped_column(default=dt.now(tz.utc))

    @hybrid_property
    def expired(self) -> bool:  # type: ignore[reportRedeclaration]
        return (
            self.created_at + timedelta(days=self.access_valid_for_days)
        ) < dt.now(tz.utc)

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

    __tablename__: ClassVar[str] = "GoCardless_requisitions"

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

    created_at: Mapped[dt] = mapped_column(default=dt.now(tz.utc))


class GoCardlessSettings(Base):
    """Stores GoCardless connection settings for a user."""

    __tablename__: ClassVar[str] = "GoCardless"

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
    last_sync: Mapped[Optional[dt]] = mapped_column(default=None)

    temp_accounts: Mapped[Optional[str]] = mapped_column(Text, default=None)

    access_token_expiration: Mapped[dt] = mapped_column(default=dt.now(tz.utc))
    refresh_token_expiration: Mapped[dt] = mapped_column(default=dt.now(tz.utc))
    enabled: Mapped[bool] = mapped_column(default=True)
    sync_frequency: Mapped[str] = mapped_column(
        String(20), default="daily"
    )  # 'daily', 'weekly', etc.
    created_at: Mapped[dt] = mapped_column(default=dt.now(tz.utc))
    updated_at: Mapped[dt] = mapped_column(
        default=dt.now(tz.utc),
        onupdate=dt.now(tz.utc),
    )

    def __repr__(self):
        """Return string representation of the GoCardless settings."""
        return f"<GoCardless settings for user {self.user_id}>"
