import csv
import io
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
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func
from werkzeug import Response
from werkzeug.datastructures.file_storage import FileStorage

from database import db
from models import (
    Budget,
    Category,
    CategoryMapping,
    CategorySplit,
    Expense,
    RecurringExpense,
)
from services.defaults import (
    create_default_categories,
    create_default_category_mappings,
)
from services.helpers import auto_categorize_transaction
from services.wrappers import login_required_dev
from session_timeout import demo_time_limited

category_bp = Blueprint("category", __name__)


@category_bp.route("/get_category_splits/<int:expense_id>")
@login_required_dev
def get_category_splits(expense_id) -> tuple[Response, int]:
    """Get category splits for an expense."""
    try:
        expense: Expense | None = db.select(Expense).get(expense_id)
        if not expense:
            abort(404)

        # Security check
        if expense.user_id != current_user.id:
            return jsonify(
                {
                    "success": False,
                    "message": "You don't have permission to view this expense",
                }
            ), 403

        if not expense.has_category_splits:
            return jsonify({"success": True, "splits": []}), 200

        # Get all category splits for this expense
        splits = (
            db.select(CategorySplit)
            .filter_by(expense_id=expense_id)
            .all()
        )

        # Format the response
        splits_data: list[dict] = []
        for split in splits:
            category: Category | None = db.select(Category).get(
                split.category_id
            )

            # Include category details if available
            category_data: dict[str, Any] = (
                {
                    "id": category.id,
                    "name": category.name,
                    "icon": category.icon,
                    "color": category.color,
                }
                if category
                else {
                    "id": None,
                    "name": "Unknown",
                    "icon": "fa-question",
                    "color": "#6c757d",
                }
            )

            splits_data.append(
                {
                    "id": split.id,
                    "category_id": split.category_id,
                    "amount": split.amount,
                    "description": split.description,
                    "category": category_data,
                }
            )

        return jsonify({"success": True, "splits": splits_data}), 200

    except Exception as e:
        current_app.logger.exception("Error getting category splits")
        return jsonify({"success": False, "message": f"Error: {e!s}"}), 500


@category_bp.route("/category_mappings")
@login_required_dev
@demo_time_limited
def manage_category_mappings():
    """View and manage category mappings for auto-categorization."""
    # Get all mappings for the current user
    mappings = (
        db.select(CategoryMapping)
        .filter_by(user_id=current_user.id)
        .order_by(
            CategoryMapping.active.desc(),
            CategoryMapping.priority.desc(),
            CategoryMapping.match_count.desc(),
        )
        .all()
    )

    # Get all categories for the dropdown
    categories = (
        db.select(Category)
        .filter_by(user_id=current_user.id)
        .order_by(Category.name)
        .all()
    )

    return render_template(
        "category_mappings.html", mappings=mappings, categories=categories
    )


@category_bp.route("/category_mappings/create_defaults", methods=["POST"])
@login_required_dev
@demo_time_limited
def create_default_mappings_route() -> tuple[Response, int]:
    """Create default category mappings for the current user (on demand)."""
    try:
        # Get current count to check if any were created
        current_count: int = (
            db.select(CategoryMapping)
            .filter_by(user_id=current_user.id)
            .count()
        )

        # Call the function to create default mappings
        create_default_category_mappings(current_user.id)

        # Get new count to see how many were created
        new_count: int = (
            db.select(CategoryMapping)
            .filter_by(user_id=current_user.id)
            .count()
        )
        created_count: int = new_count - current_count

        # Return success response
        return jsonify(
            {
                "success": True,
                "count": created_count,
                "message": f"Successfully created {created_count} default "
                "mapping rules.",
            }
        ), 200

    except Exception as e:
        current_app.logger.exception("Error creating default mappings")
        return jsonify(
            {
                "success": False,
                "message": f"Error creating default mappings: {e!s}",
            }
        ), 500


@category_bp.route("/bulk_categorize_transactions", methods=["POST"])
@login_required_dev
def bulk_categorize_transactions():
    """Categorize all uncategorized transactions using category mapping rules."""
    try:
        # Get all uncategorized transactions for the current user
        uncategorized = (
            db.select(Expense)
            .filter_by(user_id=current_user.id, category_id=None)
            .all()
        )

        # Track statistics
        total_count = len(uncategorized)
        categorized_count = 0

        # Process each transaction
        for expense in uncategorized:
            # Skip if no description
            if not expense.description:
                continue

            # Try to auto-categorize
            category_id = auto_categorize_transaction(
                expense.description, current_user.id
            )

            # If we found a category, update the transaction
            if category_id:
                expense.category_id = category_id
                categorized_count += 1

        # Save all changes
        db.session.commit()

        flash(
            f"Successfully categorized {categorized_count} out of {total_count}"
            " transactions!"
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error bulk categorizing transactions")
        flash(f"Error: {e!s}")

    # Determine where to redirect based on the referrer
    referrer: str = request.referrer
    if "transactions" in referrer:
        return redirect(url_for("transactions"))
    if "category_mappings" in referrer:
        return redirect(url_for("manage_category_mappings"))
    return redirect(url_for("dashboard"))


@category_bp.route("/category_mappings/add", methods=["POST"])
@login_required_dev
@demo_time_limited
def add_category_mapping():
    """Add a new category mapping rule."""
    keyword: str = request.form.get("keyword", "").strip()
    category_id: str | None = request.form.get("category_id")
    is_regex: bool = request.form.get("is_regex") == "on"
    priority = int(request.form.get("priority", 0))

    # Validate inputs
    if not keyword or not category_id:
        flash("Keyword and category are required.")
        return redirect(url_for("manage_category_mappings"))

    # Check if mapping already exists
    existing: CategoryMapping | None = (
        db.select(CategoryMapping)
        .filter_by(user_id=current_user.id, keyword=keyword)
        .first()
    )

    if existing:
        flash(
            "A mapping with this keyword already exists. Please edit the "
            "existing one."
        )
        return redirect(url_for("category.manage_category_mappings"))

    # Create new mapping
    mapping = CategoryMapping(
        user_id=current_user.id,
        keyword=keyword,
        category_id=int(category_id),
        is_regex=is_regex,
        priority=priority,
        active=True,
    )

    db.session.add(mapping)
    db.session.commit()

    flash("Category mapping rule added successfully.")
    return redirect(url_for("category.manage_category_mappings"))


@category_bp.route("/category_mappings/edit/<int:mapping_id>", methods=["POST"])
@login_required_dev
@demo_time_limited
def edit_category_mapping(mapping_id):
    """Edit an existing category mapping rule."""
    mapping: CategoryMapping | None = db.select(CategoryMapping).get(
        mapping_id
    )
    if not mapping:
        abort(404)

    # Check if mapping belongs to current user
    if mapping.user_id != current_user.id:
        flash("You don't have permission to edit this mapping.")
        return redirect(url_for("manage_category_mappings"))

    # Update fields
    mapping.keyword = request.form.get("keyword", "").strip()
    mapping.category_id = int(request.form.get("category_id"))  # type: ignore[]
    mapping.is_regex = request.form.get("is_regex") == "on"
    mapping.priority = int(request.form.get("priority", 0))

    db.session.commit()

    flash("Category mapping updated successfully.")
    return redirect(url_for("manage_category_mappings"))


@category_bp.route(
    "/category_mappings/toggle/<int:mapping_id>", methods=["POST"]
)
@login_required_dev
def toggle_category_mapping(mapping_id):
    """Toggle the active status of a mapping."""
    mapping: CategoryMapping | None = db.select(CategoryMapping).get(
        mapping_id
    )
    if not mapping:
        abort(404)

    # Check if mapping belongs to current user
    if mapping.user_id != current_user.id:
        flash("You don't have permission to modify this mapping.")
        return redirect(url_for("manage_category_mappings"))

    # Toggle active status
    mapping.active = not mapping.active
    db.session.commit()

    status: Literal["activated", "deactivated"] = (
        "activated" if mapping.active else "deactivated"
    )
    flash(f"Category mapping {status} successfully.")

    return redirect(url_for("manage_category_mappings"))


@category_bp.route(
    "/category_mappings/delete/<int:mapping_id>", methods=["POST"]
)
@login_required_dev
def delete_category_mapping(mapping_id) -> Response:
    """Delete a category mapping rule."""
    mapping: CategoryMapping | None = db.select(CategoryMapping).get(
        mapping_id
    )
    if not mapping:
        abort(404)

    # Check if mapping belongs to current user
    if mapping.user_id != current_user.id:
        flash("You don't have permission to delete this mapping.")
        return redirect(url_for("category.manage_category_mappings"))

    db.session.delete(mapping)
    db.session.commit()

    flash("Category mapping deleted successfully.")
    return redirect(url_for("category.manage_category_mappings"))


@category_bp.route("/category_mappings/learn_from_history", methods=["POST"])
@login_required_dev
def learn_from_transaction_history():
    """Analyze transaction history to create category mapping suggestions."""
    # Get number of days to analyze from the form
    days = int(request.form.get("days", 30))

    # Calculate start date
    start_date: datetime = datetime.now(UTC) - timedelta(days=days)

    # Get transactions from the specified period that have categories
    transactions: list[Expense] = (
        db.select(Expense)
        .filter(
            Expense.user_id == current_user.id,
            Expense.date >= start_date,
            Expense.category_id.isnot(None),
        )
        .all()
    )

    # Group transactions by category and description pattern
    patterns = {}
    for transaction in transactions:
        # Skip transactions without descriptions
        if not transaction.description:
            continue

        # Clean up description and extract a key word/phrase
        keyword = extract_keywords(transaction.description)
        if not keyword:
            continue

        # Create a unique key for this keyword + category combo
        key = f"{keyword}_{transaction.category_id}"

        if key not in patterns:
            patterns[key] = {
                "keyword": keyword,
                "category_id": transaction.category_id,
                "count": 0,
                "total_amount": 0,
                "transactions": [],
            }

        # Update the pattern
        patterns[key]["count"] += 1
        patterns[key]["total_amount"] += transaction.amount
        patterns[key]["transactions"].append(transaction.id)

    # Find significant patterns (occurred at least 3 times)
    significant_patterns = [p for p in patterns.values() if p["count"] >= 3]  # noqa: PLR2004

    # Sort by frequency
    significant_patterns.sort(key=lambda x: x["count"], reverse=True)

    # Create mappings for these patterns (only if they don't already exist)
    created_count = 0
    for pattern in significant_patterns[:15]:  # Limit to top 15
        # Check if this pattern already exists
        existing = (
            db.select(CategoryMapping)
            .filter_by(
                user_id=current_user.id,
                keyword=pattern["keyword"],
                category_id=pattern["category_id"],
            )
            .first()
        )

        if not existing:
            # Create a new mapping
            mapping = CategoryMapping(
                user_id=current_user.id,
                keyword=pattern["keyword"],
                category_id=pattern["category_id"],
                is_regex=False,
                priority=0,
                match_count=pattern["count"],
                active=True,
            )

            db.session.add(mapping)
            created_count += 1

    if created_count > 0:
        db.session.commit()
        flash(
            f"Created {created_count} new category mapping rules from your "
            "transaction history."
        )
    else:
        flash("No new mapping patterns were found in your transaction history.")

    return redirect(url_for("manage_category_mappings"))


@category_bp.route("/category_mappings/upload", methods=["POST"])
@login_required_dev
def upload_category_mappings() -> Response:  # noqa: PLR0915
    """Upload and import category mappings from a CSV file."""
    if "mapping_file" not in request.files:
        flash("No file provided")
        return redirect(url_for("manage_category_mappings"))

    mapping_file: FileStorage = request.files["mapping_file"]

    if mapping_file.filename == "":
        flash("No file selected")
        return redirect(url_for("manage_category_mappings"))

    # Case-insensitive extension check
    if mapping_file.filename and (
        not mapping_file.filename.lower().endswith(".csv")
    ):
        flash("File must be a CSV")
        return redirect(url_for("manage_category_mappings"))

    try:
        # Read file content
        file_content = mapping_file.read().decode("utf-8")

        # Parse CSV

        csv_reader = csv.DictReader(io.StringIO(file_content))
        required_fields: list[str] = ["keyword", "category"]

        # Validate CSV structure
        if not all(field in csv_reader.fieldnames for field in required_fields):  # type: ignore[]
            flash(
                "CSV must contain at least these columns: "
                f"{', '.join(required_fields)}"
            )
            return redirect(url_for("category.manage_category_mappings"))

        # Process rows
        imported_count = 0
        skipped_count = 0

        for row in csv_reader:
            try:
                # Get required fields
                keyword = row["keyword"].strip()
                category_name = row["category"].strip()

                # Get optional fields with defaults
                is_regex = str(row.get("is_regex", "false")).lower() in [
                    "true",
                    "1",
                    "yes",
                    "y",
                ]
                priority = int(row.get("priority", 0))

                # Skip empty keywords
                if not keyword or not category_name:
                    skipped_count += 1
                    continue

                # Check if mapping already exists
                existing = (
                    db.select(CategoryMapping)
                    .filter_by(user_id=current_user.id, keyword=keyword)
                    .first()
                )

                if existing:
                    # Skip duplicate mappings
                    skipped_count += 1
                    continue

                # Find the category by name (case-insensitive search)
                # First try to find exact match
                category = (
                    db.select(Category)
                    .filter(
                        Category.user_id == current_user.id,
                        func.lower(Category.name) == func.lower(category_name),
                    )
                    .first()
                )

                # If not found, try subcategories
                if not category:
                    category = (
                        db.select(Category)
                        .filter(
                            Category.user_id == current_user.id,
                            Category.parent_id.isnot(None),
                            func.lower(Category.name)
                            == func.lower(category_name),
                        )
                        .first()
                    )

                # If still not found, try partial matches
                if not category:
                    category = (
                        db.select(Category)
                        .filter(
                            Category.user_id == current_user.id,
                            func.lower(Category.name).like(
                                f"%{category_name.lower()}%"
                            ),
                        )
                        .first()
                    )

                # If no category found, use "Other"
                if not category:
                    category = (
                        db.select(Category)
                        .filter_by(
                            name="Other",
                            user_id=current_user.id,
                            is_system=True,
                        )
                        .first()
                    )

                # If we still can't find a category, skip this mapping
                if not category:
                    skipped_count += 1
                    continue

                # Create mapping
                new_mapping = CategoryMapping(
                    user_id=current_user.id,
                    keyword=keyword,
                    category_id=category.id,
                    is_regex=is_regex,
                    priority=priority,
                    match_count=0,
                    active=True,
                )

                db.session.add(new_mapping)
                imported_count += 1

            except Exception:
                current_app.logger.exception("Error importing mapping row")
                skipped_count += 1
                continue

        # Commit all successfully parsed mappings
        if imported_count > 0:
            db.session.commit()

        flash(
            f"Successfully imported {imported_count} mappings. "
            f"Skipped {skipped_count} rows."
        )

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error importing category mappings")
        flash(f"Error importing mappings: {e!s}")

    return redirect(url_for("manage_category_mappings"))


@category_bp.route("/category_mappings/export", methods=["GET"])
@login_required_dev
def export_category_mappings():
    """Export category mappings to a CSV file."""
    try:
        # Get all active mappings for the current user
        mappings = (
            db.select(CategoryMapping)
            .filter_by(user_id=current_user.id, active=True)
            .all()
        )

        if not mappings:
            flash("No active mappings to export.")
            return redirect(url_for("manage_category_mappings"))

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header row
        writer.writerow(["keyword", "category", "is_regex", "priority"])

        # Write data rows
        for mapping in mappings:
            category_name = (
                mapping.category.name if mapping.category else "Unknown"
            )
            writer.writerow(
                [
                    mapping.keyword,
                    category_name,
                    "true" if mapping.is_regex else "false",
                    mapping.priority,
                ]
            )

        # Prepare for download
        output.seek(0)

        # Generate timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"category_mappings_{timestamp}.csv"

        # Send file
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name=filename,
        )

    except Exception as e:
        current_app.logger.exception("Error exporting category mappings")
        flash(f"Error exporting mappings: {e!s}")
        return redirect(url_for("manage_category_mappings"))


def has_default_categories(user_id):
    """Check if a user already has the default category set."""
    # We'll check for a few specific default categories to
    # determine if defaults were already created
    default_category_names = [
        "Housing",
        "Food",
        "Transportation",
        "Shopping",
        "Entertainment",
        "Health",
    ]

    # Count how many of these default categories the user has
    match_count = (
        db.select(Category)
        .filter(
            Category.user_id == user_id,
            Category.name.in_(default_category_names),
            Category.parent_id.is_(None),  # Top-level categories only
        )
        .count()
    )

    # If they have at least 4 of these categories, assume defaults were created
    return match_count >= 4  # noqa: PLR2004


@category_bp.route("/categories/create_defaults", methods=["POST"])
@login_required_dev
def user_create_default_categories():
    """Allow a user to create default categories for themselves."""
    # Check if user already has default categories
    if has_default_categories(current_user.id):
        flash("You already have the default categories.")
        return redirect(url_for("category.manage_categories"))

    # Create default categories
    create_default_categories(current_user.id)
    flash("Default categories created successfully!")

    return redirect(url_for("category.manage_categories"))


@category_bp.route("/categories")
@login_required_dev
def manage_categories():
    """View and manage expense categories."""
    # Get all top-level categories
    categories: list[Category] = (
        db.select(Category)
        .filter_by(user_id=current_user.id, parent_id=None)
        .order_by(Category.name)
        .all()
    )

    # Get all FontAwesome icons for the icon picker
    icons: list[str] = [
        "fa-home",
        "fa-building",
        "fa-bolt",
        "fa-tools",
        "fa-utensils",
        "fa-shopping-basket",
        "fa-hamburger",
        "fa-coffee",
        "fa-car",
        "fa-gas-pump",
        "fa-bus",
        "fa-taxi",
        "fa-shopping-cart",
        "fa-tshirt",
        "fa-laptop",
        "fa-gift",
        "fa-film",
        "fa-ticket-alt",
        "fa-music",
        "fa-play-circle",
        "fa-heartbeat",
        "fa-stethoscope",
        "fa-prescription-bottle",
        "fa-dumbbell",
        "fa-user",
        "fa-spa",
        "fa-graduation-cap",
        "fa-question-circle",
        "fa-tag",
        "fa-money-bill",
        "fa-credit-card",
        "fa-plane",
        "fa-hotel",
        "fa-glass-cheers",
        "fa-book",
        "fa-gamepad",
        "fa-baby",
        "fa-dog",
        "fa-cat",
        "fa-phone",
        "fa-wifi",
    ]

    return render_template(
        "categories.html", categories=categories, icons=icons
    )


@category_bp.route("/categories/add", methods=["POST"])
@login_required_dev
def add_category():
    """Add a new category or subcategory."""
    name = request.form.get("name")
    icon: str = request.form.get("icon", "fa-tag")
    color: str = request.form.get("color", "#6c757d")
    parent_id = request.form.get("parent_id")
    if parent_id == "":
        parent_id = None
    if not name:
        flash("Category name is required")
        return redirect(url_for("manage_categories"))

    # Validate parent category belongs to user
    if parent_id:
        parent: Category | None = db.select(Category).get(parent_id)
        if not parent or parent.user_id != current_user.id:
            flash("Invalid parent category")
            return redirect(url_for("category.manage_categories"))

    category = Category(
        name=name,
        icon=icon,
        color=color,
        parent_id=None if not parent_id else int(parent_id),
        user_id=current_user.id,
    )

    db.session.add(category)
    db.session.commit()

    flash("Category added successfully")
    return redirect(url_for("category.manage_categories"))


@category_bp.route("/categories/edit/<int:category_id>", methods=["POST"])
@login_required_dev
def edit_category(category_id):
    """Edit an existing category."""
    category: Category | None = db.select(Category).get(category_id)
    if not category:
        abort(404)

    # Check if category belongs to current user
    if category.user_id != current_user.id:
        flash("You don't have permission to edit this category")
        return redirect(url_for("category.manage_categories"))

    # Don't allow editing system categories
    if category.is_system:
        flash("System categories cannot be edited")
        return redirect(url_for("category.manage_categories"))

    category.name = request.form.get("name", category.name)
    category.icon = request.form.get("icon", category.icon)
    category.color = request.form.get("color", category.color)

    db.session.commit()

    flash("Category updated successfully")
    return redirect(url_for("category.manage_categories"))


@category_bp.route("/categories/delete/<int:category_id>", methods=["POST"])
@login_required_dev
def delete_category(category_id):
    """Debug-enhanced category deletion route."""
    try:
        # Find the category
        category: Category | None = db.select(Category).get(category_id)
        if not category:
            abort(404)

        # Extensive logging
        current_app.logger.info(
            "Attempting to delete category: %s (ID: %d)",
            category.name,
            category.id,
        )
        current_app.logger.info(
            "Category details - User ID: %s, Is System: %s",
            category.user_id,
            category.is_system,
        )

        # Security checks
        if category.user_id != current_user.id:
            current_app.logger.warning(
                "Unauthorized delete attempt for category %d", category_id
            )
            flash("You don't have permission to delete this category")
            return redirect(url_for("category.manage_categories"))

        # Don't allow deleting system categories
        if category.is_system:
            current_app.logger.warning(
                "Attempted to delete system category %d", category_id
            )
            flash("System categories cannot be deleted")
            return redirect(url_for("category.manage_categories"))

        # Check related records before deletion
        expense_count: int = (
            db.select(Expense).filter_by(category_id=category_id).count()
        )
        recurring_count: int = (
            db.select(RecurringExpense)
            .filter_by(category_id=category_id)
            .count()
        )
        budget_count: int = (
            db.select(Budget).filter_by(category_id=category_id).count()
        )
        mapping_count: int = (
            db.select(CategoryMapping)
            .filter_by(category_id=category_id)
            .count()
        )

        current_app.logger.info(
            "Related records - Expenses: %d, Recurring: %d, Budgets: %d, "
            "Mappings: %d",
            expense_count,
            recurring_count,
            budget_count,
            mapping_count,
        )

        # Find 'Other' category (fallback)
        other_category = (
            db.select(Category)
            .filter_by(name="Other", user_id=current_user.id, is_system=True)
            .first()
        )

        current_app.logger.info(
            "Other category found: %s", bool(other_category)
        )

        # Subcategories handling
        if category.subcategories:
            current_app.logger.info(
                "Handling %d subcategories", {len(category.subcategories)}
            )
            for subcategory in category.subcategories:
                # Update or delete related records for subcategory
                db.select(Expense).filter_by(
                    category_id=subcategory.id
                ).update(
                    {
                        "category_id": other_category.id
                        if other_category
                        else None
                    }
                )
                db.select(RecurringExpense).filter_by(
                    category_id=subcategory.id
                ).update(
                    {
                        "category_id": other_category.id
                        if other_category
                        else None
                    }
                )
                db.select(CategoryMapping).filter_by(
                    category_id=subcategory.id
                ).delete()

                # Log subcategory deletion
                current_app.logger.info(
                    "Deleting subcategory: %s (ID: %d)",
                    subcategory.name,
                    subcategory.id,
                )
                db.session.delete(subcategory)

        # Update or delete main category's related records
        db.select(Expense).filter_by(category_id=category_id).update(
            {"category_id": other_category.id if other_category else None}
        )
        db.select(RecurringExpense).filter_by(
            category_id=category_id
        ).update({"category_id": other_category.id if other_category else None})
        db.select(Budget).filter_by(category_id=category_id).update(
            {"category_id": other_category.id if other_category else None}
        )
        db.select(CategoryMapping).filter_by(
            category_id=category_id
        ).delete()

        # Actually delete the category
        db.session.delete(category)

        # Commit changes
        db.session.commit()

        current_app.logger.info(
            "Category %s (ID: %d) deleted successfully",
            category.name,
            category_id,
        )
        flash("Category deleted successfully")

    except Exception as e:
        # Rollback and log any errors
        db.session.rollback()
        current_app.logger.exception("Error deleting category %d", category_id)
        flash(f"Error deleting category: {e!s}")

    return redirect(url_for("category.manage_categories"))


def update_category_mappings(transaction_id, category_id, learn=False):
    """Update category mappings based on a manually categorized transaction.

    If learn=True, create a new mapping based on this categorization
    """
    transaction: Expense | None = db.select(Expense).get(transaction_id)
    if not transaction or not category_id:
        return False

    if learn:
        # Extract a good keyword from the description
        keyword = extract_keywords(transaction.description)

        # Check if a similar mapping already exists
        existing: CategoryMapping | None = (
            db.select(CategoryMapping)
            .filter_by(
                user_id=transaction.user_id, keyword=keyword, active=True
            )
            .first()
        )

        if existing:
            # Update the existing mapping
            existing.category_id = category_id
            existing.match_count += 1
            db.session.commit()
        else:
            # Create a new mapping
            new_mapping = CategoryMapping(
                user_id=transaction.user_id,
                keyword=keyword,
                category_id=category_id,
                match_count=1,
            )
            db.session.add(new_mapping)
            db.session.commit()

        return True

    return False


def extract_keywords(description):
    """Extract meaningful keywords from a transaction description.

    :return: the most significant word or phrase
    """
    if not description:
        return ""

    # Clean up description
    clean_desc = description.strip().lower()

    # Split into words
    words = clean_desc.split()

    # Remove common words that aren't useful for categorization
    stop_words = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "on",
        "in",
        "with",
        "for",
        "to",
        "from",
        "by",
        "at",
        "of",
    }
    filtered_words = [w for w in words if w not in stop_words and len(w) > 2]  # noqa: PLR2004

    if not filtered_words:
        # If no good words remain, use the longest word from the original
        return max(words, key=len) if words else ""

    # Use the longest remaining word as the keyword
    # This is a simple approach - could be improved with more sophisticated NLP
    return max(filtered_words, key=len)


@category_bp.route("/api/categories")
@login_required_dev
def get_categories_api() -> tuple[Response, int]:
    """Fetch categories for the current user."""
    try:
        # Get all categories for the current user
        categories: list[Category] = (
            db.select(Category).filter_by(user_id=current_user.id).all()
        )

        # Convert to JSON-serializable format
        result: list[dict[str, Any]] = [
            {
                "id": category.id,
                "name": category.name,
                "icon": category.icon,
                "color": category.color,
                "parent_id": category.parent_id,
                # Add this to help with displaying in the UI
                "is_parent": category.parent_id is None,
            }
            for category in categories
        ]

        return jsonify(result), 200
    except Exception as e:
        current_app.logger.exception("Error fetching categories")
        return jsonify({"error": str(e)}), 500
