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
from flask_mail import Message
from sqlalchemy import or_

from extensions import db, mail
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
    Tag,
    User,
)
from services.defaults import create_default_budgets, create_default_categories
from services.wrappers import login_required_dev
from simplefin_client import SimpleFin

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin")
@login_required_dev
def admin():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard"))

    users = User.query.all()
    return render_template("admin.html", users=users)


@admin_bp.route("/admin/add_user", methods=["POST"])
@login_required_dev
def admin_add_user():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard"))

    email = request.form.get("email")
    password = request.form.get("password")
    name = request.form.get("name")
    is_admin = request.form.get("is_admin") == "on"

    if User.query.filter_by(id=email).first():
        flash("Email already registered")
        return redirect(url_for("admin"))

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
    return redirect(url_for("admin"))


@admin_bp.route("/admin/delete_user/<user_id>", methods=["POST"])
@login_required_dev
def admin_delete_user(user_id):  # noqa: PLR0915
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard"))

    if user_id == current_user.id:
        flash("Cannot delete your own admin account!")
        return redirect(url_for("admin"))

    current_app.logger.info("Starting deletion process for user: %s", user_id)

    user = User.query.filter_by(id=user_id).first()
    if not user:
        flash("User not found.")
        return redirect(url_for("admin"))

    try:
        # Start a transaction
        current_app.logger.info("Starting transaction")

        # Delete all related data in the correct order
        # 1. First handle budgets (they reference categories)
        current_app.logger.info("Deleting budgets...")
        Budget.query.filter_by(user_id=user_id).delete()

        # 2. Delete recurring expenses
        current_app.logger.info("Deleting recurring expenses...")
        RecurringExpense.query.filter_by(user_id=user_id).delete()

        # 3. Delete expenses
        current_app.logger.info("Deleting expenses...")
        Expense.query.filter_by(user_id=user_id).delete()

        # 4. Delete settlements
        current_app.logger.info("Deleting settlements...")
        Settlement.query.filter(
            or_(
                Settlement.payer_id == user_id,
                Settlement.receiver_id == user_id,
            )
        ).delete(synchronize_session=False)

        # 5. Delete category mappings
        current_app.logger.info("Deleting category mappings...")
        CategoryMapping.query.filter_by(user_id=user_id).delete()

        # 6. Delete SimpleFin settings
        current_app.logger.info("Deleting SimpleFin settings...")
        SimpleFin.query.filter_by(user_id=user_id).delete()

        # 7. Delete ignored recurring patterns
        current_app.logger.info("Deleting ignored patterns...")
        IgnoredRecurringPattern.query.filter_by(user_id=user_id).delete()

        # 8. Handle user's accounts
        current_app.logger.info("Deleting accounts...")
        Account.query.filter_by(user_id=user_id).delete()

        # 9. Handle tags - first remove from association table
        current_app.logger.info("Handling tags...")
        user_tags = Tag.query.filter_by(user_id=user_id).all()
        for tag in user_tags:
            # Clear association with expenses
            tag.expenses = []
        db.session.flush()

        # Now delete the tags
        Tag.query.filter_by(user_id=user_id).delete()

        # 10. Categories can now be deleted
        current_app.logger.info("Deleting categories...")
        Category.query.filter_by(user_id=user_id).delete()

        # 11. Handle group memberships
        current_app.logger.info("Handling group memberships...")
        # First, handle groups created by this user
        for group in Group.query.filter_by(created_by=user_id).all():
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
        current_app.logger.exception("Error deleting user:", exc_info=True)
        flash(f"Error deleting user: {e!s}")

    return redirect(url_for("admin"))


@admin_bp.route("/admin/reset_password", methods=["POST"])
@login_required_dev
def admin_reset_password():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard"))

    user_id = request.form.get("user_id")
    new_password = request.form.get("new_password")
    confirm_password = request.form.get("confirm_password")

    # Validate passwords match
    if new_password != confirm_password:
        flash("Passwords do not match!")
        return redirect(url_for("admin"))

    user = User.query.filter_by(id=user_id).first()
    if user:
        user.set_password(new_password)
        db.session.commit()
        flash(f"Password reset successful for {user.name}!")
    else:
        flash("User not found.")

    return redirect(url_for("admin"))


@admin_bp.route("/admin/toggle_admin_status/<user_id>", methods=["POST"])
@login_required_dev
def admin_toggle_admin_status(user_id):
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.")
        return redirect(url_for("dashboard"))

    # Prevent changing your own admin status
    if user_id == current_user.id:
        flash("Cannot change your own admin status!")
        return redirect(url_for("admin"))

    user = User.query.filter_by(id=user_id).first()
    if not user:
        flash("User not found.")
        return redirect(url_for("admin"))

    # Toggle admin status
    user.is_admin = not user.is_admin
    db.session.commit()

    status = "admin" if user.is_admin else "regular user"
    flash(f"User {user.name} is now a {status}!")

    return redirect(url_for("admin"))
