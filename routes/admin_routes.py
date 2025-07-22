from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy import or_
from werkzeug import Response

from database import db
from models import (
    Account,
    Budget,
    Category,
    CategoryMapping,
    Expense,
    Group,
    IgnoredRecurringPattern,
    RecurringExpense,
    Settlement,
    SimpleFinSettings,
    Tag,
    User,
)
from services.defaults import create_default_budgets, create_default_categories
from services.helpers import send_welcome_email
from services.wrappers import login_required_dev

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/")
@login_required_dev
def admin() -> Response | str:
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard.dashboard"))

    users: list[User] = db.select(User).all()
    return render_template("admin.html", users=users)


@admin_bp.route("/admin/add_user", methods=["POST"])
@login_required_dev
def add_user():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard.dashboard"))

    email: str = request.form.get("email") or ""
    password: str = request.form.get("password") or ""
    name: str = request.form.get("name") or ""
    is_admin: bool = request.form.get("is_admin") == "on"

    if db.select(User).filter_by(id=email).first():
        flash("Email already registered")
        return redirect(url_for("admin.admin"))

    user = User(id=email, name=name, is_admin=is_admin)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    create_default_categories(user.id)
    db.session.commit()

    create_default_budgets(user.id)
    db.session.commit()

    try:
        send_welcome_email(user)
    except Exception:
        current_app.logger.exception("Failed to send welcome email")

    flash("User added successfully!")
    return redirect(url_for("admin.admin"))


@admin_bp.route("/delete_user/<user_id>", methods=["POST"])
@login_required_dev
def delete_user(user_id):  # noqa: PLR0915
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard.dashboard"))

    if user_id == current_user.id:
        flash("Cannot delete your own admin account!")
        return redirect(url_for("admin.admin"))

    current_app.logger.info("Starting deletion process for user: %s", user_id)

    user: User | None = db.select(User).filter_by(id=user_id).first()
    if not user:
        flash("User not found.")
        return redirect(url_for("admin.admin"))

    try:
        # Start a transaction
        current_app.logger.info("Starting transaction")

        # Delete all related data in the correct order
        # 1. First handle budgets (they reference categories)
        current_app.logger.info("Deleting budgets...")
        db.select(Budget).filter_by(user_id=user_id).delete()

        # 2. Delete recurring expenses
        current_app.logger.info("Deleting recurring expenses...")
        db.select(RecurringExpense).filter_by(user_id=user_id).delete()

        # 3. Delete expenses
        current_app.logger.info("Deleting expenses...")
        db.select(Expense).filter_by(user_id=user_id).delete()

        # 4. Delete settlements
        current_app.logger.info("Deleting settlements...")
        db.select(Settlement).filter(
            or_(
                Settlement.payer_id == user_id,
                Settlement.receiver_id == user_id,
            )
        ).delete(synchronize_session=False)

        # 5. Delete category mappings
        current_app.logger.info("Deleting category mappings...")
        db.select(CategoryMapping).filter_by(user_id=user_id).delete()

        # 6. Delete SimpleFin settings
        current_app.logger.info("Deleting SimpleFin settings...")
        db.select(SimpleFinSettings).filter_by(user_id=user_id).delete()

        # 7. Delete ignored recurring patterns
        current_app.logger.info("Deleting ignored patterns...")
        db.select(IgnoredRecurringPattern).filter_by(
            user_id=user_id
        ).delete()

        # 8. Handle user's accounts
        current_app.logger.info("Deleting accounts...")
        db.select(Account).filter_by(user_id=user_id).delete()

        # 9. Handle tags - first remove from association table
        current_app.logger.info("Handling tags...")
        user_tags = db.select(Tag).filter_by(user_id=user_id).all()
        for tag in user_tags:
            # Clear association with expenses
            tag.expenses = []
        db.session.flush()

        # Now delete the tags
        db.select(Tag).filter_by(user_id=user_id).delete()

        # 10. Categories can now be deleted
        current_app.logger.info("Deleting categories...")
        db.select(Category).filter_by(user_id=user_id).delete()

        # 11. Handle group memberships
        current_app.logger.info("Handling group memberships...")
        # First, handle groups created by this user
        for group in (
            db.select(Group).filter_by(created_by=user_id).all()
        ):
            # Remove the user from the group members if they're in it
            if user in group.members:
                group.members.remove(user)

            # Find a new owner or delete the group if empty
            if group.members:
                # Assign first remaining member as new owner
                new_owner = next(iter(group.members))
                group.created_by = new_owner.id
            else:
                # Delete group if no members left
                db.session.delete(group)

        # Remove user from all groups they're a member of
        for group in user.groups:
            group.members.remove(user)

        # 12. Finally delete the user
        current_app.logger.info("Deleting user...")
        db.session.delete(user)

        # Commit all changes
        db.session.commit()
        current_app.logger.info("User %s deleted successfully", user_id)
        flash("User deleted successfully!")

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error deleting user")
        flash(f"Error deleting user: {e!s}")

    return redirect(url_for("admin.admin"))


@admin_bp.route("/reset_password", methods=["POST"])
@login_required_dev
def reset_password():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard.dashboard"))

    user_id = request.form.get("user_id")
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")

    # Validate passwords match
    if new_password != confirm_password:
        flash("Passwords do not match!")
        return redirect(url_for("admin.admin"))

    user = db.select(User).filter_by(id=user_id).first()
    if user:
        user.set_password(new_password)
        db.session.commit()
        flash(f"Password reset successful for {user.name}!")
    else:
        flash("User not found.")

    return redirect(url_for("admin.admin"))


@admin_bp.route("/toggle_admin_status/<user_id>", methods=["POST"])
@login_required_dev
def toggle_admin_status(user_id):
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard.dashboard"))

    # Prevent changing your own admin status
    if user_id == current_user.id:
        flash("Cannot change your own admin status!")
        return redirect(url_for("admin.admin"))

    user = db.select(User).filter_by(id=user_id).first()
    if not user:
        flash("User not found.")
        return redirect(url_for("admin.admin"))

    # Toggle admin status
    user.is_admin = not user.is_admin
    db.session.commit()

    status = "admin" if user.is_admin else "regular user"
    flash(f"User {user.name} is now a {status}!")

    return redirect(url_for("admin.admin"))
