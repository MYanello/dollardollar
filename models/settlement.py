from datetime import datetime
from datetime import timezone as tz
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from .base import Base

if TYPE_CHECKING:
    from models import User


class Settlement(Base):
    """Stores settlement information."""

    __tablename__: ClassVar[str] = "settlements"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    payer_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("users.id"), nullable=False
    )
    receiver_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("users.id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(nullable=False)

    # Relationships
    payer: Mapped["User"] = relationship(
        "User",
        foreign_keys=[payer_id],
        back_populates="settlements_paid",
        lazy=True,
        init=False,
    )
    receiver: Mapped["User"] = relationship(
        "User",
        foreign_keys=[receiver_id],
        back_populates="settlements_received",
        lazy=True,
        init=False,
    )

    date: Mapped[datetime] = mapped_column(
        nullable=False, default=datetime.now(tz.utc)
    )
    description: Mapped[str] = mapped_column(
        String(200), nullable=True, default="Settlement"
    )
