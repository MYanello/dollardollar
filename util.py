from re import Pattern

from flask import Flask, request
from flask_login import current_user
from sqlalchemy import func, inspect, text
from sqlalchemy.engine.reflection import Inspector

from database import db
from models import Account, Category, CategoryMapping


def detect_internal_transfer(description, amount, account_id=None):
    """
    Detect if a transaction appears to be an internal transfer between accounts
    Returns a tuple of (is_transfer, source_account_id, destination_account_id)
    """
    # Default return values
    is_transfer = False
    source_account_id = account_id
    destination_account_id = None

    # Skip if no description or account
    if not description or not account_id:
        return is_transfer, source_account_id, destination_account_id

    # Normalize description for easier matching
    desc_lower = description.lower()

    # Common transfer-related keywords
    transfer_keywords = [
        "transfer",
        "xfer",
        "move",
        "moved to",
        "sent to",
        "to account",
        "from account",
        "between accounts",
        "internal",
        "account to account",
        "trx to",
        "trx from",
        "trans to",
        "trans from",
        "ACH Withdrawal",
        "Robinhood",
        "BK OF AMER VISA ONLINE PMT",
        "Payment Thank You",
    ]

    # Check for transfer keywords in description
    if any(keyword in desc_lower for keyword in transfer_keywords):
        is_transfer = True

        # Try to identify the destination account
        # Get all user accounts
        user_accounts = (
            db.session.query(Account)
            .filter(Account.user_id == current_user.id)
            .all()
        )

        # Look for account names in the description
        for account in user_accounts:
            # Skip the source account
            if account.id == account_id:
                continue

            # Check if account name appears in the description
            if account.name.lower() in desc_lower:
                # This is likely the destination account
                destination_account_id = account.id
                break

    return is_transfer, source_account_id, destination_account_id


def auto_categorize_transaction(description: str, user_id) -> None | int:  # noqa: C901
    """
    Automatically categorize a transaction based on its description
    Returns the best matching category ID or None if no match found
    """
    if not description:
        return None

    # Standardize description - lowercase and remove extra spaces
    description = description.strip().lower()

    # Get all active category mappings for the user
    mappings: list[CategoryMapping] = (
        db.session.query(CategoryMapping)
        .filter_by(user_id=user_id, active=True)
        .order_by(
            CategoryMapping.priority.desc(), CategoryMapping.match_count.desc()
        )
        .all()
    )

    # Keep track of matches and their scores
    matches: list[tuple[CategoryMapping, int]] = []

    # Check each mapping
    for mapping in mappings:
        matched = False
        if mapping.is_regex:
            # Use regex pattern matching
            try:
                import re

                pattern: Pattern[str] = re.compile(
                    mapping.keyword, re.IGNORECASE
                )
                if pattern.search(description):
                    matched = True
            except:
                # If regex is invalid, fall back to simple substring search
                matched: bool = mapping.keyword.lower() in description
        else:
            # Simple substring matching
            matched = mapping.keyword.lower() in description

        if matched:
            # Calculate match score based on:
            # 1. Priority (user-defined importance)
            # 2. Usage count (previous successful matches)
            # 3. Keyword length (longer keywords are more specific)
            # 4. Keyword position (earlier in the string is better)
            score: int = (
                (mapping.priority * 100)
                + (mapping.match_count * 10)
                + len(mapping.keyword)
            )

            # Adjust score based on position (if simple keyword)
            if not mapping.is_regex:
                position: int = description.find(mapping.keyword.lower())
                if position == 0:  # Matches at the start
                    score += 50
                elif position > 0:  # Adjust based on how early it appears
                    score += max(0, 30 - position)

            matches.append((mapping, score))

    # Sort matches by score, descending
    matches.sort(key=lambda x: x[1], reverse=True)

    # If we have any matches, increment the match count for the winner and
    # return its category ID
    if matches:
        best_mapping = matches[0][0]
        best_mapping.match_count += 1
        db.session.commit()
        return best_mapping.category_id

    return None


def get_category_id(  # noqa: PLR0911
    category_name: str, description=None, user_id=None
) -> int | None:
    """Find, create, or auto-suggest a category based on name and description."""
    # Clean the category name
    category_name = category_name.strip() if category_name else ""

    # If we have a user ID and no category name but have a description
    if user_id and not category_name and description:
        # Try to auto-categorize based on description
        auto_category_id: None | int = auto_categorize_transaction(
            description, user_id
        )
        if auto_category_id:
            return auto_category_id

    # If we have a category name, try to find it
    if category_name:
        # Try to find an exact match first
        category: Category | None = (
            db.session.query(Category)
            .filter(
                Category.user_id == user_id if user_id else current_user.id,
                func.lower(Category.name) == func.lower(category_name),
            )
            .first()
        )

        if category:
            return category.id

        # Try to find a partial match in subcategories
        subcategory: Category | None = (
            db.session.query(Category)
            .filter(
                Category.user_id == user_id if user_id else current_user.id,
                Category.parent_id.isnot(None),
                func.lower(Category.name).like(f"%{category_name.lower()}%"),
            )
            .first()
        )

        if subcategory:
            return subcategory.id

        # Try to find a partial match in parent categories
        parent_category: Category | None = (
            db.session.query(Category)
            .filter(
                Category.user_id == user_id if user_id else current_user.id,
                Category.parent_id.is_(None),
                func.lower(Category.name).like(f"%{category_name.lower()}%"),
            )
            .first()
        )

        if parent_category:
            return parent_category.id

        # If auto-categorize is enabled, create a new category
        if "auto_categorize" in request.form:
            # Find "Other" category as parent
            other_category: Category | None = (
                db.session.query(Category)
                .filter_by(
                    name="Other",
                    user_id=user_id if user_id else current_user.id,
                    is_system=True,
                )
                .first()
            )

            new_category = Category(
                name=category_name[:50],  # Limit to 50 chars
                icon="fa-tag",
                color="#6c757d",
                parent_id=other_category.id if other_category else None,
                user_id=user_id if user_id else current_user.id,
            )

            db.session.add(new_category)
            db.session.flush()  # Get ID without committing

            return new_category.id

    # If we still don't have a category, try auto-categorization again with the
    # description
    if description and user_id:
        # Try to auto-categorize based on description
        auto_category_id = auto_categorize_transaction(description, user_id)
        if auto_category_id:
            return auto_category_id

    # Default to None if no match found and auto-categorize is off
    return None


def normalize_time_series(data, target_length):
    """Normalize a time series to a target length for better comparison."""
    if len(data) == 0:
        return [0] * target_length

    if len(data) == target_length:
        return data

    # Use resampling to normalize the data
    result: list[float | int] = []
    ratio: float = len(data) / target_length

    for i in range(target_length):
        start_idx = int(i * ratio)
        end_idx = int((i + 1) * ratio)
        end_idx: int = min(end_idx, len(data))

        if start_idx == end_idx:
            segment_avg = data[start_idx] if start_idx < len(data) else 0
        else:
            segment_avg: float = sum(data[start_idx:end_idx]) / (
                end_idx - start_idx
            )

        result.append(segment_avg)

    return result


def get_category_name(expense) -> str | None:
    """Get the category name for an expense."""
    if hasattr(expense, "category_id") and expense.category_id:
        category: Category | None = db.session.query(Category).get(
            expense.category_id
        )
        if category:
            return category.name
    return None


def process_daily_spending(expenses, start_date, end_date) -> list[float]:
    """Process expenses into daily totals."""
    days: int = (end_date - start_date).days + 1
    daily_spending: list[float] = [0] * days

    for expense in expenses:
        day_index: int = (expense["date"] - start_date).days
        if 0 <= day_index < days:
            daily_spending[day_index] += expense["user_portion"]

    return daily_spending


def check_db_structure(app: Flask):
    """Check database structure and add any missing columns.

    This function runs before the first request to ensure the database schema
    is up-to-date.
    """
    with app.app_context():
        app.logger.info("Checking database structure...")
        inspector: Inspector = inspect(db.engine)

        # Check User model for user_color column
        users_columns: list[str] = [
            col["name"] for col in inspector.get_columns("users")
        ]
        if "user_color" not in users_columns:
            app.logger.warning(
                "Missing user_color column in users table - adding it now"
            )
            db.session.execute(
                text(
                    "ALTER TABLE users ADD COLUMN user_color VARCHAR(7) "
                    'DEFAULT "#15803d"'
                )
            )
            db.session.commit()
            app.logger.info("Added user_color column to users table")

        # Check for OIDC columns
        if "oidc_id" not in users_columns:
            app.logger.warning(
                "Missing oidc_id column in users table - adding it now"
            )
            db.session.execute(
                text("ALTER TABLE users ADD COLUMN oidc_id VARCHAR(255)")
            )
            db.session.commit()
            app.logger.info("Added oidc_id column to users table")

            # Create index on oidc_id column
            indexes: list[str | None] = [
                idx["name"] for idx in inspector.get_indexes("users")
            ]
            if "ix_users_oidc_id" not in indexes:
                db.session.execute(
                    text(
                        "CREATE UNIQUE INDEX ix_users_oidc_id ON users (oidc_id)"
                    )
                )
                db.session.commit()
                app.logger.info("Created index on oidc_id column")

        if "oidc_provider" not in users_columns:
            app.logger.warning(
                "Missing oidc_provider column in users table - adding it now"
            )
            db.session.execute(
                text("ALTER TABLE users ADD COLUMN oidc_provider VARCHAR(50)")
            )
            db.session.commit()
            app.logger.info("Added oidc_provider column to users table")

        if "last_login" not in users_columns:
            app.logger.warning(
                "Missing last_login column in users table - adding it now"
            )
            # Change DATETIME to TIMESTAMP for PostgreSQL compatibility
            db.session.execute(
                text("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")
            )
            db.session.commit()
            app.logger.info("Added last_login column to users table")

        app.logger.info("Database structure check completed")
