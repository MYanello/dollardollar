import secrets
from datetime import datetime, timedelta
from datetime import timezone as tz
from typing import TYPE_CHECKING, ClassVar, Optional

from flask_login import UserMixin
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import db
from tables import group_users

from .group import Group

if TYPE_CHECKING:
    from models import (
        Account,
        Budget,
        Category,
        CategoryMapping,
        Currency,
        Expense,
        GoCardlessSettings,
        IgnoredRecurringPattern,
        RecurringExpense,
        Requisition,
        Settlement,
        SimpleFinSettings,
        Tag,
    )


class User(db.Model, UserMixin):
    """Stores user information and settings."""

    __tablename__: ClassVar[str] = "users"

    id: Mapped[str] = mapped_column(
        String(120), primary_key=True
    )  # Using email as ID

    name: Mapped[str] = mapped_column(String(100))
    expenses: Mapped[Optional["Expense"]] = relationship(
        "Expense", back_populates="user", lazy=True
    )
    default_currency: Mapped[Optional["Currency"]] = relationship(
        "Currency", back_populates="users", lazy=True
    )
    created_groups: Mapped[Optional["Group"]] = relationship(
        "Group",
        back_populates="creator",
        lazy=True,
        foreign_keys=[Group.created_by],
    )
    accounts: Mapped[list["Account"]] = relationship(
        "Account", back_populates="user", lazy=True
    )

    budgets: Mapped[list["Budget"]] = relationship(
        "Budget", back_populates="user", lazy=True
    )

    categories: Mapped[list["Category"]] = relationship(
        "Category", back_populates="user", lazy=True
    )

    category_mappings: Mapped[list["CategoryMapping"]] = relationship(
        "CategoryMapping", back_populates="user", lazy=True
    )

    groups: Mapped[list["Group"]] = relationship(
        "Group",
        back_populates="members",
        secondary=group_users,
        lazy="subquery",
    )

    requisitions: Mapped[list["Requisition"]] = relationship(
        "Requisition", back_populates="user", lazy=True
    )

    gocardless: Mapped["GoCardlessSettings"] = relationship(
        "GoCardlessSettings", back_populates="user", lazy=True
    )

    recurring_expenses: Mapped[list["RecurringExpense"]] = relationship(
        "RecurringExpense", back_populates="user", lazy=True
    )

    ignored_patterns: Mapped[list["IgnoredRecurringPattern"]] = relationship(
        "IgnoredRecurringPattern", back_populates="user", lazy=True
    )

    settlements_paid: Mapped[list["Settlement"]] = relationship(
        "Settlement",
        back_populates="payer",
        foreign_keys="Settlement.payer_id",
        lazy=True
    )

    settlements_received: Mapped[list["Settlement"]] = relationship(
        "Settlement",
        back_populates="receiver",
        foreign_keys="Settlement.receiver_id",
        lazy=True,
    )

    simplefin: Mapped[Optional["SimpleFinSettings"]] = relationship(
        "SimpleFinSettings", back_populates="user", lazy=True
    )

    tags: Mapped[list["Tag"]] = relationship(
        "Tag", back_populates="user", lazy=True
    )
    password_hash: Mapped[Optional[str]] = mapped_column(
        String(256), default=None
    )
    reset_token: Mapped[Optional[str]] = mapped_column(
        String(100), default=None
    )
    reset_token_expiry: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, default=None
    )
    default_currency_code: Mapped[Optional[str]] = mapped_column(
        String(3), ForeignKey("currencies.code"), default=None
    )
    # OIDC related fields
    oidc_id: Mapped[Optional[str]] = mapped_column(
        String(255), index=True, unique=True, default=None
    )
    oidc_provider: Mapped[Optional[str]] = mapped_column(
        String(50), default=None
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(default=None)

    user_color: Mapped[str] = mapped_column(String(7), default="#15803d")

    is_admin: Mapped[bool] = mapped_column(default=False)
    monthly_report_enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now(tz.utc))
    timezone: Mapped[str] = mapped_column(
        String(50), nullable=True, default="UTC"
    )
    def set_password(self, password):
        """Hash password and store it."""
        self.password_hash = generate_password_hash(
            password, method="pbkdf2:sha256"
        )

    def check_password(self, password):
        """Check password against stored password hash."""
        # No password set
        if not self.password_hash:
            return False
        try:
            return check_password_hash(self.password_hash, password)
        except ValueError:
            return False

    def generate_reset_token(self):
        """Generate a password reset token that expires in 1 hour."""
        self.reset_token = secrets.token_urlsafe(32)
        self.reset_token_expiry = datetime.now(tz.utc) + timedelta(hours=1)
        return self.reset_token

    def verify_reset_token(self, token):
        """Verify if the provided token is valid and not expired."""
        if not self.reset_token or self.reset_token != token:
            return False
        return not (
            not self.reset_token_expiry
            or self.reset_token_expiry < datetime.now(tz.utc)
        )

    def clear_reset_token(self):
        """Clear the reset token and expiry after use."""
        self.reset_token = "None"
        self.reset_token_expiry = datetime.now(tz.utc)
