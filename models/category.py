from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from .base import Base

if TYPE_CHECKING:
    from models import Budget, Expense, RecurringExpense, User


class Category(Base):
    """Stores category information."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)

    user_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("users.id"), nullable=False
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship(
        "User", back_populates="categories", lazy=True, init=False
    )

    parent: Mapped[Optional["Category"]] = relationship(
        "Category",
        remote_side=[id],
        back_populates="subcategories",
        lazy=True,
        init=False,
    )

    expenses: Mapped[Optional["Expense"]] = relationship(
        "Expense", back_populates="category", lazy=True, init=False
    )

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id"), default=None
    )

    icon: Mapped[str] = mapped_column(
        String(50), default="fa-tag"
    )  # FontAwesome icon name
    color: Mapped[str] = mapped_column(String(20), default="#6c757d")

    is_system: Mapped[bool] = mapped_column(
        default=False
    )  # System categories can't be deleted
    created_at: Mapped[datetime] = mapped_column(default=datetime.now(UTC))

    budgets: Mapped[list["Budget"]] = relationship(
        "Budget", back_populates="category", lazy=True, init=False
    )

    subcategories: Mapped[list["Category"]] = relationship(
        "Category", back_populates="parent", lazy=True, init=False
    )

    splits: Mapped["CategorySplit"] = relationship(
        "CategorySplit", back_populates="category", lazy=True, init=False
    )

    mappings: Mapped["CategoryMapping"] = relationship(
        "CategoryMapping", back_populates="category", lazy=True, init=False
    )

    recurring_expenses: Mapped[list["RecurringExpense"]] = relationship(
        "RecurringExpense", back_populates="category", lazy=True, init=False
    )

    def __repr__(self) -> str:
        """Return string representation of category information."""
        return f"<Category: {self.name}>"


class CategorySplit(Base):
    """Stores category split information."""

    __tablename__ = "category_splits"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    expense_id: Mapped[int] = mapped_column(
        ForeignKey("expenses.id"), nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(
        String(200), nullable=True, default=None
    )

    # Relationships
    expense: Mapped["Expense"] = relationship(
        "Expense",
        back_populates="category_splits",
        lazy=True,
        init=False,
    )
    category: Mapped["Category"] = relationship(
        "Category", back_populates="splits", lazy=True, init=False
    )


class CategoryMapping(Base):
    """Stores information about a category mapping."""

    __tablename__ = "category_mappings"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    user_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("users.id"), nullable=False
    )
    keyword: Mapped[str] = mapped_column(String(100), nullable=False)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id"), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User", back_populates="category_mappings", lazy=True, init=False
    )
    category: Mapped[Optional["Category"]] = relationship(
        "Category", back_populates="mappings", lazy=True, init=False
    )

    is_regex: Mapped[bool] = mapped_column(
        default=False
    )  # Whether the keyword is a regex pattern
    priority: Mapped[int] = mapped_column(
        default=0
    )  # Higher priority mappings take precedence
    match_count: Mapped[int] = mapped_column(
        default=0
    )  # How many times this mapping has been used
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now(UTC))

    def __repr__(self) -> str:
        """Return string representation of category mapping information."""
        return (
            f"<CategoryMapping: '{self.keyword}' → "
            f"{self.category.name if self.category else self.category_id}>"
        )
