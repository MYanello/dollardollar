from datetime import datetime
from datetime import timezone as tz
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from models import Account, Expense, RecurringExpense, User


class Currency(Base):
    """Stores currency information."""

    __tablename__: ClassVar[str] = "currencies"

    code: Mapped[str] = mapped_column(
        String(3), primary_key=True
    )  # ISO 4217 currency code (e.g., USD, EUR, GBP)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(5), nullable=False)
    rate_to_base: Mapped[float] = mapped_column(
        nullable=False, default=1.0
    )  # Exchange rate to base currency
    is_base: Mapped[bool] = mapped_column(
        default=False
    )  # Whether this is the base currency
    last_updated: Mapped[datetime] = mapped_column(default=datetime.now(tz.utc))

    accounts: Mapped[list["Account"]] = relationship(
        "Account", back_populates="currency", lazy=True, init=False
    )

    users: Mapped[list["User"]] = relationship(
        "User", back_populates="default_currency", lazy=True, init=False
    )

    expenses: Mapped[list["Expense"]] = relationship(
        "Expense", back_populates="currency", lazy=True, init=False
    )

    recurring_expenses: Mapped[list["RecurringExpense"]] = relationship(
        "RecurringExpense", back_populates="currency", lazy=True, init=False
    )

    def __repr__(self) -> str:
        """Return string representation of Currency information."""
        return f"{self.code} ({self.symbol})"
