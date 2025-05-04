from datetime import datetime
from datetime import timezone as tz
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from tables import group_users

from .base import Base

if TYPE_CHECKING:
    from models import Expense, RecurringExpense, User


class Group(Base):
    """Stores group information and settings."""

    __tablename__: ClassVar[str] = "groups"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(
        String(120), ForeignKey("users.id"), nullable=False
    )
    members: Mapped[list["User"]] = relationship(
        "User",
        secondary=group_users,
        lazy="subquery",
        back_populates="groups",
        default_factory=list,
    )
    expenses: Mapped[list["Expense"]] = relationship(
        "Expense",
        back_populates="group",
        lazy=True,
        default_factory=list,
    )
    created_at: Mapped[datetime] = mapped_column(default=datetime.now(tz.utc))

    creator: Mapped["User"] = relationship(
        "User",
        back_populates="created_groups",
        foreign_keys=[created_by],
        lazy=True,
        init=False,
    )

    recurring_expenses: Mapped["RecurringExpense"] = relationship(
        "RecurringExpense", back_populates="group", lazy=True, init=False
    )
