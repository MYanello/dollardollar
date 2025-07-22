import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from werkzeug import Response

from database import db
from models import Budget, Category, CategorySplit, Expense
from services.helpers import calculate_category_spending, get_base_currency
from services.wrappers import login_required_dev
from session_timeout import demo_time_limited

budget_bp = Blueprint("budget", __name__)


@budget_bp.route("/")
@login_required_dev
@demo_time_limited
def budgets() -> str:
    """View and manage budgets."""
    # Get all budgets for the current user
    user_budgets: list[Budget] = (
        db.session.query(Budget)
        .filter_by(user_id=current_user.id)
        .order_by(Budget.created_at.desc())
        .all()
    )

    # Get all categories for the form
    categories: list[Category] = (
        db.session.query(Category)
        .filter_by(user_id=current_user.id)
        .order_by(Category.name)
        .all()
    )

    # Calculate budget progress for each budget
    budget_data = []
    total_month_budget = 0
    total_month_spent = 0

    for budget in user_budgets:
        spent = budget.calculate_spent_amount()
        remaining = budget.get_remaining_amount()
        percentage = budget.get_progress_percentage()
        status: Literal["over", "under", "approaching"] = budget.get_status()

        period_start: datetime
        period_end: datetime
        period_start, period_end = budget.get_current_period_dates()

        budget_data.append(
            {
                "budget": budget,
                "spent": spent,
                "remaining": remaining,
                "percentage": percentage,
                "status": status,
                "period_start": period_start,
                "period_end": period_end,
            }
        )

        # Add to monthly totals only for monthly budgets
        if budget.period == "monthly":
            total_month_budget += budget.amount
            total_month_spent += spent

    # Get base currency for display
    base_currency = get_base_currency()

    # Pass the current date to the template
    now: datetime = datetime.now(UTC)

    return render_template(
        "budgets.html",
        budget_data=budget_data,
        categories=categories,
        base_currency=base_currency,
        total_month_budget=total_month_budget,
        total_month_spent=total_month_spent,
        now=now,
    )


@budget_bp.route("/add", methods=["POST"])
@login_required_dev
def add_budget() -> Response:
    """Add a new budget."""
    try:
        # Get form data
        category_id: str = request.form.get("category_id")  # type: ignore[]
        amount = float(request.form.get("amount", 0))
        period: str = request.form.get("period", "monthly")
        include_subcategories: bool = (
            request.form.get("include_subcategories") == "on"
        )
        name: str = request.form.get("name", "").strip() or None  # type: ignore[]
        start_date_str: str = request.form.get("start_date")  # type: ignore[]
        is_recurring: bool = request.form.get("is_recurring") == "on"

        # Validate category exists
        category: Category | None = (
            db.session.query(Category).filter_by(id=category_id).first()
        )
        if not category or category.user_id != current_user.id:
            flash("Invalid category selected.")
            return redirect(url_for("budget.budgets"))

        # Parse start date
        try:
            start_date: datetime = (
                datetime.strptime(start_date_str, "%Y-%m-%d").replace(
                    tzinfo=UTC
                )
                if start_date_str
                else datetime.now(UTC)
            )
        except ValueError:
            start_date = datetime.now(UTC)

        # Check if a budget already exists for this category
        existing_budget: Budget | None = (
            db.session.query(Budget)
            .filter_by(
                user_id=current_user.id,
                category_id=category_id,
                period=period,
                active=True,
            )
            .first()
        )

        if existing_budget:
            flash(
                f"An active {period} budget already exists for this category. "
                f"Please edit or deactivate it first."
            )
            return redirect(url_for("budget.budgets"))

        # Create new budget
        budget = Budget(
            user_id=current_user.id,
            category_id=category_id,  # type: ignore[]
            name=name,
            amount=amount,
            period=period,
            include_subcategories=include_subcategories,
            start_date=start_date,
            is_recurring=is_recurring,
            active=True,
        )

        db.session.add(budget)
        db.session.commit()

        flash("Budget added successfully!")

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error adding budget")
        flash(f"Error adding budget: {e!s}")

    return redirect(url_for("budget.budgets"))


@budget_bp.route("/edit/<int:budget_id>", methods=["POST"])
@login_required_dev
def edit_budget(budget_id) -> Response:
    """Edit an existing budget."""
    try:
        # Find the budget
        budget: Budget | None = (
            db.session.query(Budget).filter_by(id=budget_id).first()
        )
        if not budget:
            abort(404)

        # Security check
        if budget.user_id != current_user.id:
            flash("You do not have permission to edit this budget.")
            return redirect(url_for("budget.budgets"))

        # Update fields
        budget.category_id = int(
            request.form.get("category_id", budget.category_id)
        )
        budget.name = request.form.get("name", "").strip() or budget.name
        budget.amount = float(request.form.get("amount", budget.amount))
        budget.period = request.form.get("period", budget.period)
        budget.include_subcategories = (
            request.form.get("include_subcategories") == "on"
        )

        if request.form.get("start_date"):
            with contextlib.suppress(
                ValueError
            ):  # Keep original if parsing fails
                budget.start_date = datetime.strptime(
                    request.form.get("start_date"),  # type: ignore[]
                    "%Y-%m-%d",
                ).replace(tzinfo=UTC)

        budget.is_recurring = request.form.get("is_recurring") == "on"
        budget.updated_at = datetime.now(UTC)

        db.session.commit()
        flash("Budget updated successfully!")

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error updating budget")
        flash(f"Error updating budget: {e!s}")

    return redirect(url_for("budget.budgets"))


@budget_bp.route("/toggle/<int:budget_id>", methods=["POST"])
@login_required_dev
def toggle_budget(budget_id) -> Response:
    """Toggle budget active status."""
    try:
        # Find the budget
        budget: Budget | None = (
            db.session.query(Budget).filter_by(id=budget_id).first()
        )
        if not budget:
            abort(404)

        # Security check
        if budget.user_id != current_user.id:
            flash("You do not have permission to modify this budget.")
            return redirect(url_for("budget.budgets"))

        # Toggle active status
        budget.active = not budget.active
        db.session.commit()

        status: Literal["activated", "deactivated"] = (
            "activated" if budget.active else "deactivated"
        )
        flash(f"Budget {status} successfully!")

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error toggling budget")
        flash(f"Error toggling budget: {e!s}")

    return redirect(url_for("budget.budgets"))


@budget_bp.route("/delete/<int:budget_id>", methods=["POST"])
@login_required_dev
def delete_budget(budget_id) -> Response:
    """Delete a budget."""
    try:
        # Find the budget
        budget: Budget | None = (
            db.session.query(Budget).filter_by(id=budget_id).first()
        )
        if not budget:
            abort(404)

        # Security check
        if budget.user_id != current_user.id:
            flash("You do not have permission to delete this budget.")
            return redirect(url_for("budget.budgets"))

        db.session.delete(budget)
        db.session.commit()

        flash("Budget deleted successfully!")

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error deleting budget")
        flash(f"Error deleting budget: {e!s}")

    return redirect(url_for("budget.budgets"))


@budget_bp.route("/get/<int:budget_id>", methods=["GET"])
@login_required_dev
def get_budget(budget_id) -> tuple[Response, int]:
    """Get budget details for editing via AJAX."""
    try:
        # Find the budget
        budget: Budget | None = (
            db.session.query(Budget).filter_by(id=budget_id).first()
        )
        if not budget:
            abort(404)

        # Security check
        if budget.user_id != current_user.id:
            return jsonify(
                {
                    "success": False,
                    "message": "You do not have permission to view this budget",
                }
            ), 403

        # Get category details
        category: Category | None = (
            db.session.query(Category).filter_by(id=budget.category_id).first()
        )
        category_name: str = category.name if category else "Unknown"

        # Format dates
        start_date: str = budget.start_date.strftime("%Y-%m-%d")

        # Calculate spent amount
        spent = budget.calculate_spent_amount()
        remaining = budget.get_remaining_amount()
        percentage = budget.get_progress_percentage()
        status: Literal["over", "approaching", "under"] = budget.get_status()

        # Return the budget data
        return jsonify(
            {
                "success": True,
                "budget": {
                    "id": budget.id,
                    "name": budget.name or "",
                    "category_id": budget.category_id,
                    "category_name": category_name,
                    "amount": budget.amount,
                    "period": budget.period,
                    "include_subcategories": budget.include_subcategories,
                    "start_date": start_date,
                    "is_recurring": budget.is_recurring,
                    "active": budget.active,
                    "spent": spent,
                    "remaining": remaining,
                    "percentage": percentage,
                    "status": status,
                },
            }
        ), 200

    except Exception as e:
        current_app.logger.exception("Error retrieving budget %s", budget_id)
        return jsonify({"success": False, "message": f"Error: {e!s}"}), 500


def get_budget_summary() -> dict[str, Any]:
    """Get budget summary for current user."""
    # Get all active budgets
    active_budgets: list[Budget] = (
        db.session.query(Budget)
        .filter_by(user_id=current_user.id, active=True)
        .all()
    )

    # Process budget data
    budget_summary: dict[str, Any] = {
        "total_budgets": len(active_budgets),
        "over_budget": 0,
        "approaching_limit": 0,
        "under_budget": 0,
        "alert_budgets": [],  # For budgets that are over or approaching limit
    }

    for budget in active_budgets:
        status: Literal["over", "approaching", "under"] = budget.get_status()
        if status == "over":
            budget_summary["over_budget"] += 1
            budget_summary["alert_budgets"].append(
                {
                    "id": budget.id,
                    "name": budget.name
                    or (budget.category.name if budget.category else "Unknown"),
                    "percentage": budget.get_progress_percentage(),
                    "status": status,
                    "amount": budget.amount,
                    "spent": budget.calculate_spent_amount(),
                }
            )
        elif status == "approaching":
            budget_summary["approaching_limit"] += 1
            budget_summary["alert_budgets"].append(
                {
                    "id": budget.id,
                    "name": budget.name
                    or (budget.category.name if budget.category else "Unknown"),
                    "percentage": budget.get_progress_percentage(),
                    "status": status,
                    "amount": budget.amount,
                    "spent": budget.calculate_spent_amount(),
                }
            )
        else:
            budget_summary["under_budget"] += 1

    # Sort alert budgets by percentage (highest first)
    budget_summary["alert_budgets"] = sorted(
        budget_summary["alert_budgets"],
        key=lambda x: x["percentage"],
        reverse=True,
    )

    return budget_summary


@budget_bp.route("/subcategory-spending/<int:budget_id>")
@login_required_dev
def get_subcategory_spending(budget_id) -> tuple[Response, int]:
    """Get spending details for subcategories of a budget category."""
    try:
        # Find the budget
        budget: Budget | None = (
            db.session.query(Budget).filter_by(id=budget_id).first()
        )
        if not budget:
            abort(404)

        # Security check
        if budget.user_id != current_user.id:
            return jsonify(
                {
                    "success": False,
                    "message": "You do not have permission to view this budget",
                }
            ), 403

        # Get the base currency symbol
        base_currency = get_base_currency()

        # Check if base_currency is a dictionary or an object
        currency_symbol = (
            base_currency["symbol"]
            if isinstance(base_currency, dict)
            else base_currency.symbol
        )

        # Get the category and its subcategories
        category: Category | None = (
            db.session.query(Category).filter_by(id=budget.category_id).first()
        )
        if not category:
            return jsonify(
                {"success": False, "message": "Category not found"}
            ), 404

        subcategories: list[dict] = []

        # Get period dates for this budget
        period_start: datetime
        period_end: datetime
        period_start, period_end = budget.get_current_period_dates()

        # If this budget includes the parent category directly
        if not budget.include_subcategories:
            # Only include the parent category itself
            spent = calculate_category_spending(
                category.id, period_start, period_end
            )

            subcategories.append(
                {
                    "id": category.id,
                    "name": category.name,
                    "icon": category.icon,
                    "color": category.color,
                    "spent": spent,
                }
            )
        else:
            # Include all subcategories
            for subcategory in category.subcategories:
                spent = calculate_category_spending(
                    subcategory.id, period_start, period_end
                )

                subcategories.append(
                    {
                        "id": subcategory.id,
                        "name": subcategory.name,
                        "icon": subcategory.icon,
                        "color": subcategory.color,
                        "spent": spent,
                    }
                )

            # If the parent category itself has direct expenses, add it too
            spent = calculate_category_spending(
                category.id,
                period_start,
                period_end,
                include_subcategories=False,
            )

            if spent > 0:
                subcategories.append(
                    {
                        "id": category.id,
                        "name": f"{category.name} (direct)",
                        "icon": category.icon,
                        "color": category.color,
                        "spent": spent,
                    }
                )

        # Sort subcategories by spent amount (highest first)
        subcategories = sorted(
            subcategories, key=lambda x: x["spent"], reverse=True
        )

        return jsonify(
            {
                "success": True,
                "budget_id": budget.id,
                "budget_amount": float(budget.amount),
                "currency_symbol": currency_symbol,
                "subcategories": subcategories,
            }
        ), 200

    except Exception as e:
        current_app.logger.exception(
            "Error retrieving subcategory spending for budget %s", budget_id
        )
        return jsonify({"success": False, "message": f"Error: {e!s}"}), 500


@budget_bp.route("/trends-data")
@login_required_dev
def budget_trends_data() -> tuple[Response, int]:  # noqa: PLR0915
    """Get budget trends data for chart visualization."""
    budget_id: str | None = request.args.get("budget_id")

    # Default time period (last 6 months)
    end_date: datetime = datetime.now(UTC)
    start_date: datetime = end_date - timedelta(days=180)

    # Prepare response data structure
    response: dict[str, list[Any]] = {
        "labels": [],
        "actual": [],
        "budget": [],
        "colors": [],
    }

    # Generate monthly labels
    current_date: datetime = start_date
    while current_date <= end_date:
        month_label: str = current_date.strftime("%b %Y")
        response["labels"].append(month_label)
        current_date = (
            current_date.replace(day=28) + timedelta(days=4)
        ).replace(day=1)

    # If no budget selected, return all budgets aggregated by month
    if not budget_id:
        # Get all active budgets
        budgets = (
            db.session.query(Budget)
            .filter_by(user_id=current_user.id, active=True)
            .all()
        )
        current_app.logger.debug("Found %d active budgets", len(budgets))

        # For each month, calculate total budget and spending
        for _, month in enumerate(response["labels"]):
            month_date: datetime = datetime.strptime(month, "%b %Y").replace(
                tzinfo=UTC
            )
            month_start: datetime = month_date.replace(day=1)
            if month_date.month == 12:  # noqa: PLR2004
                month_end: datetime = month_date.replace(
                    year=month_date.year + 1, month=1, day=1
                ) - timedelta(days=1)
            else:
                month_end = month_date.replace(
                    month=month_date.month + 1, day=1
                ) - timedelta(days=1)

            # Sum all budgets for this month
            monthly_budget = 0
            for budget in budgets:
                if budget.period == "monthly":
                    monthly_budget += budget.amount
                elif budget.period == "yearly":
                    monthly_budget += budget.amount / 12
                elif budget.period == "weekly":
                    # Calculate weeks in this month
                    weeks_in_month: float = (month_end - month_start).days / 7
                    monthly_budget += budget.amount * weeks_in_month

            response["budget"].append(monthly_budget)
            current_app.logger.debug(
                "Month %s: Budget amount = %f", month, monthly_budget
            )

            # Calculate actual spending for this month
            monthly_spent = 0

            # 1. Get regular expenses without splits
            # (no category splits, no user splits)
            direct_expenses = (
                db.session.query(Expense)
                .filter(
                    Expense.user_id == current_user.id,
                    Expense.date >= month_start,
                    Expense.date <= month_end,
                    ~(Expense.has_category_splits),
                    Expense.split_with.is_(None) | (Expense.split_with == ""),
                )
                .all()
            )

            # Add up direct expenses (no splits)
            direct_total = 0
            for expense in direct_expenses:
                amount: float = getattr(expense, "amount_base", expense.amount)
                direct_total += amount

            monthly_spent += direct_total
            current_app.logger.debug(
                "Month %s: Direct expenses (no splits) = %f",
                month,
                direct_total,
            )

            # 2. Get expenses that have user splits but no category splits
            user_split_expenses: list[Expense] = (
                db.session.query(Expense)
                .filter(
                    Expense.user_id == current_user.id,
                    Expense.date >= month_start,
                    Expense.date <= month_end,
                    ~(Expense.has_category_splits),
                    Expense.split_with.isnot(None) & (Expense.split_with != ""),
                )
                .all()
            )

            # Calculate user's portion for each user split expense
            user_split_total = 0
            for expense in user_split_expenses:
                # Get split information with error handling
                try:
                    split_info = expense.calculate_splits()
                    if not split_info:
                        continue

                    # Calculate user's portion
                    user_amount = 0

                    # Check if user is payer and not in split list
                    if expense.paid_by == current_user.id and (
                        not expense.split_with
                        or current_user.id not in expense.split_with.split(",")
                    ):
                        user_amount = split_info["payer"]["amount"]
                    else:
                        # Look for user in splits
                        for split in split_info["splits"]:
                            if split["email"] == current_user.id:
                                user_amount = split["amount"]
                                break

                    user_split_total += user_amount

                except Exception:
                    current_app.logger.exception(
                        "Error calculating splits for expense %d", expense.id
                    )
                    # Fallback: Just divide by number of participants
                    # (including payer)
                    participants = 1  # Start with payer
                    if expense.split_with:
                        participants += len(expense.split_with.split(","))

                    if participants > 0:
                        user_split_total += expense.amount / participants

            monthly_spent += user_split_total
            current_app.logger.debug(
                "Month %s: User split expenses = %f", month, user_split_total
            )

            # 3. Get expenses with category splits
            split_expenses: list[Expense] = (
                db.session.query(Expense)
                .filter(
                    Expense.user_id == current_user.id,
                    Expense.date >= month_start,
                    Expense.date <= month_end,
                    ~(Expense.has_category_splits),
                )
                .all()
            )

            # Process each split expense
            category_split_total = 0
            for split_expense in split_expenses:
                # Get category splits for this expense
                category_splits: list[CategorySplit] = (
                    db.session.query(CategorySplit)
                    .filter_by(expense_id=split_expense.id)
                    .all()
                )

                # Calculate the base amount from category splits
                split_amount: float = sum(
                    split.amount for split in category_splits
                )

                # If expense also has user splits, calculate user's portion
                if (
                    split_expense.split_with
                    and split_expense.split_with.strip()
                ):
                    try:
                        split_info: dict[str, Any] = (
                            split_expense.calculate_splits()
                        )
                        if not split_info:
                            # If no split info, treat as full amount
                            category_split_total += split_amount
                            continue

                        # Calculate user's percentage of the total expense
                        user_percentage = 0

                        # Check if user is payer and not in split list
                        if (
                            split_expense.paid_by == current_user.id
                            and current_user.id
                            not in split_expense.split_with.split(",")
                        ):
                            user_percentage = (
                                split_info["payer"]["amount"]
                                / split_expense.amount
                                if split_expense.amount > 0
                                else 0
                            )
                        else:
                            # Look for user in splits
                            for split in split_info["splits"]:
                                if split["email"] == current_user.id:
                                    user_percentage = (
                                        split["amount"] / split_expense.amount
                                        if split_expense.amount > 0
                                        else 0
                                    )
                                    break

                        # Apply user's percentage to the category amount
                        category_split_total += split_amount * user_percentage

                    except Exception:
                        current_app.logger.exception(
                            "Error calculating splits for expense %d",
                            split_expense.id,
                        )
                        # Fallback: Just divide by number of participants
                        participants = 1  # Start with payer
                        if split_expense.split_with:
                            participants += len(
                                split_expense.split_with.split(",")
                            )

                        if participants > 0:
                            category_split_total += split_amount / participants
                else:
                    # No user splits, use the full category split amount
                    category_split_total += split_amount

            monthly_spent += category_split_total
            current_app.logger.debug(
                "Month %s: Category split expenses = %f",
                month,
                category_split_total,
            )

            # Add the calculated amounts to the response
            response["actual"].append(monthly_spent)

            # Set color based on whether spending exceeds budget
            color: Literal["#ef4444", "#22c55e"] = (
                "#ef4444" if monthly_spent > monthly_budget else "#22c55e"
            )
            response["colors"].append(color)

            current_app.logger.debug(
                "Month %s: Total monthly spent = %f", month, monthly_spent
            )
    else:
        # Get specific budget
        budget: Budget | None = (
            db.session.query(Budget).filter_by(id=budget_id).first()
        )
        if not budget:
            abort(404)

        # Security check
        if budget.user_id != current_user.id:
            return jsonify({"error": "Unauthorized"}), 403

        current_app.logger.debug(
            "Processing trends for single budget %d: {budget.name or 'Unnamed'}"
            ", amount=%f",
            budget_id,
            budget.amount,
        )

        # Process each month
        for _, month in enumerate(response["labels"]):
            month_date = datetime.strptime(month, "%b %Y").replace(tzinfo=UTC)
            month_start = month_date.replace(day=1)
            if month_date.month == 12:  # noqa: PLR2004
                month_end = month_date.replace(
                    year=month_date.year + 1, month=1, day=1
                ) - timedelta(days=1)
            else:
                month_end = month_date.replace(
                    month=month_date.month + 1, day=1
                ) - timedelta(days=1)

            # Get budget amount for this month
            monthly_budget = 0
            if budget.period == "monthly":
                monthly_budget = budget.amount
            elif budget.period == "yearly":
                monthly_budget = budget.amount / 12
            elif budget.period == "weekly":
                # Calculate weeks in this month
                weeks_in_month = (month_end - month_start).days / 7
                monthly_budget = budget.amount * weeks_in_month

            response["budget"].append(monthly_budget)
            current_app.logger.debug(
                "Month %s: Budget amount = %f", month, monthly_budget
            )

            # Create list of categories to include
            category_ids: list[int] = [budget.category_id]
            if budget.include_subcategories and budget.category:
                category_ids.extend(
                    [subcat.id for subcat in budget.category.subcategories]
                )

            # Calculate spending for this category
            monthly_spent = 0

            # 1. Get direct expenses (not split by category, not split by user)
            direct_expenses: list[Expense] = (
                db.session.query(Expense)
                .filter(
                    Expense.user_id == current_user.id,
                    Expense.date >= month_start,
                    Expense.date <= month_end,
                    Expense.category_id.in_(category_ids),
                    ~(Expense.has_category_splits),
                    Expense.split_with.is_(None) | (Expense.split_with == ""),
                )
                .all()
            )

            # Add up direct expenses
            direct_total = 0
            for expense in direct_expenses:
                amount = getattr(expense, "amount_base", expense.amount)
                direct_total += amount

            monthly_spent += direct_total
            current_app.logger.debug(
                "Month %s: Direct expenses (no splits) = %f",
                month,
                direct_total,
            )

            # 2. Get expenses with user splits but not category splits
            user_split_expenses = (
                db.session.query(Expense)
                .filter(
                    Expense.user_id == current_user.id,
                    Expense.date >= month_start,
                    Expense.date <= month_end,
                    Expense.category_id.in_(category_ids),
                    ~(Expense.has_category_splits),
                    Expense.split_with.isnot(None) & (Expense.split_with != ""),
                )
                .all()
            )

            # Process user split expenses
            user_split_total = 0
            for expense in user_split_expenses:
                try:
                    # Get split information
                    split_info = expense.calculate_splits()
                    if not split_info:
                        continue

                    # Calculate user's portion
                    user_amount = 0

                    # Check if user is payer and not in split list
                    if expense.paid_by == current_user.id and (
                        not expense.split_with
                        or current_user.id not in expense.split_with.split(",")
                    ):
                        user_amount = split_info["payer"]["amount"]
                    else:
                        # Look for user in splits
                        for split in split_info["splits"]:
                            if split["email"] == current_user.id:
                                user_amount = split["amount"]
                                break

                    user_split_total += user_amount

                except Exception:
                    current_app.logger.exception(
                        "Error calculating splits for expense %d", expense.id
                    )
                    # Fallback: Just divide by number of participants
                    # (including payer)
                    participants = 1  # Start with payer
                    if expense.split_with:
                        participants += len(expense.split_with.split(","))

                    if participants > 0:
                        user_split_total += expense.amount / participants

            monthly_spent += user_split_total
            current_app.logger.debug(
                "Month %s: User split expenses = %f", month, user_split_total
            )

            # 3. Get category splits for these categories
            category_splits = (
                db.session.query(CategorySplit)
                .join(Expense)
                .filter(
                    Expense.user_id == current_user.id,
                    Expense.date >= month_start,
                    Expense.date <= month_end,
                    CategorySplit.category_id.in_(category_ids),
                )
                .all()
            )

            # Group by expense to avoid double counting
            expense_splits: dict[int, list[CategorySplit]] = {}
            for split in category_splits:
                if split.expense_id not in expense_splits:
                    expense_splits[split.expense_id] = []
                expense_splits[split.expense_id].append(split)

            # Process each expense with category splits
            category_split_total = 0
            for expense_id, splits in expense_splits.items():
                try:
                    # Get the expense
                    expense: Expense | None = db.session.query(Expense).get(
                        expense_id
                    )
                    if not expense:
                        continue

                    # Calculate total relevant split amount
                    relevant_amount: float = sum(
                        split.amount for split in splits
                    )

                    # If expense also has user splits, calculate user's portion
                    if expense.split_with and expense.split_with.strip():
                        split_info = expense.calculate_splits()
                        if not split_info:
                            # If no split info, treat as full amount
                            category_split_total += relevant_amount
                            continue

                        # Calculate user's percentage of the total expense
                        user_percentage = 0

                        # Check if user is payer and not in split list
                        if (
                            expense.paid_by == current_user.id
                            and current_user.id
                            not in expense.split_with.split(",")
                        ):
                            user_percentage: float = (
                                split_info["payer"]["amount"] / expense.amount
                                if expense.amount > 0
                                else 0
                            )
                        else:
                            # Look for user in splits
                            for split in split_info["splits"]:
                                if split["email"] == current_user.id:
                                    user_percentage = (
                                        split["amount"] / expense.amount
                                        if expense.amount > 0
                                        else 0
                                    )
                                    break

                        # Apply user's percentage to the category amount
                        user_portion: float | int = (
                            relevant_amount * user_percentage
                        )
                        category_split_total += user_portion
                        current_app.logger.debug(
                            "Expense %d: User percentage %f, Amount %f, "
                            "User portion %f",
                            expense_id,
                            user_percentage,
                            relevant_amount,
                            user_portion,
                        )

                    else:
                        # No user splits, use the full category split amount
                        category_split_total += relevant_amount

                except Exception:
                    current_app.logger.exception(
                        "Error processing expense %d with category splits",
                        expense_id,
                    )
                    # Fallback: Just divide by number of participants
                    expense = db.session.query(Expense).get(expense_id)
                    if expense and expense.split_with:
                        participants = 1 + len(expense.split_with.split(","))
                        relevant_amount = sum(split.amount for split in splits)
                        category_split_total += relevant_amount / participants
                    else:
                        # If no split info or can't get expense, add full amount
                        category_split_total += sum(
                            split.amount for split in splits
                        )

            monthly_spent += category_split_total
            current_app.logger.debug(
                "Month %s: Category split expenses = %f",
                month,
                category_split_total,
            )

            # Add the calculated amounts to the response
            response["actual"].append(monthly_spent)

            # Set color based on whether spending exceeds budget
            color = "#ef4444" if monthly_spent > monthly_budget else "#22c55e"
            response["colors"].append(color)

            current_app.logger.debug(
                "Month %s: Total monthly spent = %f, Budget = %f",
                month,
                monthly_spent,
                monthly_budget,
            )

    # Debug log the final response data
    current_app.logger.debug(
        "Budget trends response: labels=%s", response["labels"]
    )
    current_app.logger.debug(
        "Budget trends response: budget=%s", response["budget"]
    )
    current_app.logger.debug(
        "Budget trends response: actual=%s", response["actual"]
    )

    return jsonify(response), 200


@budget_bp.route("/transactions/<int:budget_id>")
@login_required_dev
@demo_time_limited
def budget_transactions(budget_id) -> tuple[Response, int]:  # noqa: PLR0915
    """Get transactions related to a specific budget with proper split handling."""
    # Get the budget
    budget: Budget | None = (
        db.session.query(Budget).filter_by(id=budget_id).first()
    )
    if not budget:
        abort(404)

    # Security check
    if budget.user_id != current_user.id:
        return jsonify({"error": "Unauthorized"}), 403

    # Default time period (last 30 days)
    end_date: datetime = datetime.now(tz=UTC)
    start_date: datetime = end_date - timedelta(days=30)

    transactions: list[Any] = []

    # Create list of categories to include
    category_ids: list[int] = [budget.category_id]
    if budget.include_subcategories and budget.category:
        category_ids.extend(
            [subcat.id for subcat in budget.category.subcategories]
        )

    # 1. Get expenses directly assigned to these categories
    # (not split by category)
    direct_expenses = (
        db.session.query(Expense)
        .filter(
            Expense.user_id == current_user.id,
            Expense.date >= start_date,
            Expense.category_id.in_(category_ids),
            ~(Expense.has_category_splits),
        )
        .order_by(Expense.date.desc())
        .all()
    )

    # 2. Get expenses with category splits for these categories
    split_expenses_query = (
        db.session.query(Expense)
        .join(CategorySplit, Expense.id == CategorySplit.expense_id)
        .filter(
            Expense.user_id == current_user.id,
            Expense.date >= start_date,
            CategorySplit.category_id.in_(category_ids),
        )
        .order_by(Expense.date.desc())
        .distinct()
    )

    # Combine and sort expenses by date
    all_expenses: list[Expense] = sorted(
        list(direct_expenses) + list(split_expenses_query.all()),
        key=lambda x: x.date,
        reverse=True,
    )[:50]  # Limit to 50 transactions

    # Format transactions for the response
    for expense in all_expenses:
        # Initialize basic transaction info
        transaction: dict[str, Any] = {
            "id": expense.id,
            "description": expense.description,
            "date": expense.date.strftime("%Y-%m-%d"),
            "card_used": expense.card_used,
            "transaction_type": expense.transaction_type,
            "has_category_splits": expense.has_category_splits,
            "has_user_splits": bool(
                expense.split_with and expense.split_with.strip()
            ),
        }

        # Get split information if needed
        split_info: dict[str, Any] | None = (
            expense.calculate_splits()
            if transaction["has_user_splits"]
            else None
        )

        # Handle amount based on split type
        if expense.has_category_splits:
            # Get category splits relevant to this budget
            relevant_cat_splits: list[CategorySplit] = (
                db.session.query(CategorySplit)
                .filter(
                    CategorySplit.expense_id == expense.id,
                    CategorySplit.category_id.in_(category_ids),
                )
                .all()
            )

            # Calculate relevant category amount
            relevant_cat_amount: float = sum(
                split.amount for split in relevant_cat_splits
            )

            # If expense also has user splits, calculate the user's portion
            if transaction["has_user_splits"] and split_info:
                # Calculate user's percentage of the total expense
                user_percentage = 0

                # Check if user is payer and not in split list
                if (
                    expense.paid_by == current_user.id
                    and expense.split_with
                    and current_user.id not in expense.split_with.split(",")
                ):
                    user_percentage: float = (
                        split_info["payer"]["amount"] / expense.amount
                        if expense.amount > 0
                        else 0
                    )
                else:
                    # Look for user in splits
                    for split in split_info["splits"]:
                        if split["email"] == current_user.id:
                            user_percentage = (
                                split["amount"] / expense.amount
                                if expense.amount > 0
                                else 0
                            )
                            break

                # Apply user's percentage to the category amount
                transaction["amount"] = relevant_cat_amount * user_percentage

                # Add split details for display
                transaction["split_details"] = {
                    "total_users": len(split_info["splits"])
                    + (1 if split_info["payer"]["amount"] > 0 else 0),
                    "user_portion_percentage": user_percentage * 100,
                    "category_amount": relevant_cat_amount,
                    "user_amount": transaction["amount"],
                    "total_amount": expense.amount,
                }
            else:
                # No user splits, use the full category amount
                transaction["amount"] = relevant_cat_amount

            # Add category information from the first relevant split
            if relevant_cat_splits:
                cat_id: int = relevant_cat_splits[0].category_id
                category: Category | None = db.session.query(Category).get(
                    cat_id
                )
                if category:
                    transaction["category_name"] = category.name
                    transaction["category_icon"] = category.icon
                    transaction["category_color"] = category.color
                else:
                    transaction["category_name"] = "Split Category"
                    transaction["category_icon"] = "fa-tags"
                    transaction["category_color"] = "#6c757d"
        else:
            # For non-category-split expenses
            if transaction["has_user_splits"] and split_info:
                # Calculate user's portion
                user_amount = 0

                # Check if user is payer and not in split list
                if (
                    expense.paid_by == current_user.id
                    and expense.split_with
                    and current_user.id not in expense.split_with.split(",")
                ):
                    user_amount = split_info["payer"]["amount"]
                else:
                    # Look for user in splits
                    for split in split_info["splits"]:
                        if split["email"] == current_user.id:
                            user_amount: float = split["amount"]
                            break

                transaction["amount"] = user_amount

                # Add split details for display
                total_users: int = len(split_info["splits"]) + (
                    1 if split_info["payer"]["amount"] > 0 else 0
                )
                transaction["split_details"] = {
                    "total_users": total_users,
                    "user_portion_percentage": (
                        user_amount / expense.amount * 100
                    )
                    if expense.amount > 0
                    else 0,
                    "user_amount": user_amount,
                    "total_amount": expense.amount,
                }
            else:
                # Regular non-split expense, use the full amount
                transaction["amount"] = expense.amount

            # Add category information
            if expense.category_id:
                category = db.session.query(Category).get(expense.category_id)
                if category:
                    transaction["category_name"] = category.name
                    transaction["category_icon"] = category.icon
                    transaction["category_color"] = category.color
                else:
                    transaction["category_name"] = "Uncategorized"
                    transaction["category_icon"] = "fa-tag"
                    transaction["category_color"] = "#6c757d"
            else:
                transaction["category_name"] = "Uncategorized"
                transaction["category_icon"] = "fa-tag"
                transaction["category_color"] = "#6c757d"

        # Add the original total amount for reference
        transaction["original_amount"] = expense.amount

        # Add tags if available
        if hasattr(expense, "tags") and expense.tags:
            transaction["tags"] = [tag.name for tag in expense.tags]

        transactions.append(transaction)

    return jsonify(
        {
            "transactions": transactions,
            "budget_id": budget_id,
            "budget_name": budget.name
            or (budget.category.name if budget.category else "Budget"),
        }
    ), 200


@budget_bp.route("/summary-data")
@login_required_dev
def budget_summary_data() -> tuple[Response, int]:
    """Get budget summary data for charts and displays."""
    try:
        # Get all active monthly budgets
        monthly_budgets: list[Budget] = (
            db.session.query(Budget)
            .filter_by(user_id=current_user.id, period="monthly", active=True)
            .all()
        )

        # Calculate totals
        total_budget: float = sum(budget.amount for budget in monthly_budgets)
        total_spent: float = sum(
            budget.calculate_spent_amount() for budget in monthly_budgets
        )

        # Get budgets with their data
        budget_data: list[Any] = []
        for budget in monthly_budgets:
            spent = budget.calculate_spent_amount()
            percentage = budget.get_progress_percentage()
            status: Literal["over", "approaching", "under"] = (
                budget.get_status()
            )

            # Get category info
            category: Category | None = db.session.query(Category).get(
                budget.category_id
            )
            category_name: str = category.name if category else "Unknown"
            category_color: str = category.color if category else "#6c757d"

            budget_data.append(
                {
                    "id": budget.id,
                    "name": budget.name or category_name,
                    "amount": budget.amount,
                    "spent": spent,
                    "percentage": percentage,
                    "status": status,
                    "color": category_color,
                }
            )

        return jsonify(
            {
                "success": True,
                "total_budget": total_budget,
                "total_spent": total_spent,
                "budgets": budget_data,
            }
        ), 200

    except Exception as e:
        current_app.logger.exception("Error retrieving budget summary data")
        return jsonify({"success": False, "message": f"Error: {e!s}"}), 500
