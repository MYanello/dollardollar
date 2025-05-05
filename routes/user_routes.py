import datetime
import re

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
from werkzeug import Response

from database import db
from models import Currency
from services.wrappers import login_required_dev

user_bp = Blueprint("user", __name__)


@user_bp.route("/")
@login_required_dev
def profile() -> str:
    """User profile page with settings to change password and personal color."""
    # Get user's account creation date
    # (approximating from join date since we don't store it)
    account_created: str | datetime.datetime = (
        current_user.created_at.strftime("%Y-%m-%d")
        if current_user.created_at
        else "Account creation date not available"
    )

    # Get user color (default to app's primary green if not set)
    user_color: str = (
        current_user.user_color
        if hasattr(current_user, "user_color") and current_user.user_color
        else "#15803d"
    )

    # Get available currencies for default currency selection
    currencies: list[Currency] = db.session.query(Currency).all()

    # Check if OIDC is enabled
    oidc_enabled: bool = current_app.config.get("OIDC_ENABLED", False)

    return render_template(
        "profile.html",
        user_color=user_color,
        account_created=account_created,
        currencies=currencies,
        oidc_enabled=oidc_enabled,
    )


@user_bp.route("/change_password", methods=["POST"])
@login_required_dev
def change_password() -> Response:
    """Handle password change request."""
    current_password: str = request.form.get("current_password")  # type: ignore[]
    new_password: str = request.form.get("new_password")  # type: ignore[]
    confirm_password: str = request.form.get("confirm_password")  # type: ignore[]

    # Validate inputs
    if not current_password or not new_password or not confirm_password:
        flash("All password fields are required")
        return redirect(url_for("user.profile"))

    if new_password != confirm_password:
        flash("New passwords do not match")
        return redirect(url_for("user.profile"))

    # Verify current password
    if not current_user.check_password(current_password):
        flash("Current password is incorrect")
        return redirect(url_for("user.profile"))

    # Set new password
    current_user.set_password(new_password)
    db.session.commit()

    flash("Password updated successfully")
    return redirect(url_for("user.profile"))


@user_bp.route("/update_color", methods=["POST"])
@login_required_dev
def update_color() -> Response:
    """Update user's personal color."""
    # Retrieve color from form, defaulting to primary green
    user_color = request.form.get("user_color", "#15803d").strip()

    # Validate hex color format (supports 3 or 6 digit hex colors)
    hex_pattern = r"^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$"
    if not user_color or not re.match(hex_pattern, user_color):
        flash("Invalid color format. Please use a valid hex color code.")
        return redirect(url_for("user.profile"))

    # Normalize to 6-digit hex if 3-digit shorthand is used
    if len(user_color) == 4:  # RGB format  # noqa: PLR2004
        user_color: str = "#" + "".join(2 * c for c in user_color[1:])

    # Update user's color
    current_user.user_color = user_color
    db.session.commit()

    flash("Your personal color has been updated")
    return redirect(url_for("user.profile"))
