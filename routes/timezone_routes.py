import pytz
from flask import Blueprint, flash, redirect, request, url_for
from flask_login import current_user

from database import db
from services.wrappers import login_required_dev

timezone_bp = Blueprint("timezone", __name__)


@timezone_bp.route("/update_timezone", methods=["POST"])
@login_required_dev
def update_timezone():
    """Update user's timezone preference."""
    timezone = request.form.get("timezone")

    # Validate timezone
    if timezone not in pytz.all_timezones:
        flash("Invalid timezone selected.")
        return redirect(url_for("profile"))

    # Update user's timezone
    current_user.timezone = timezone
    db.session.commit()

    flash("Timezone updated successfully.")
    return redirect(url_for("profile"))


# Utility functions for timezone handling
def get_user_timezone(user):
    """Get user's timezone, defaulting to UTC."""
    return pytz.timezone(user.timezone or "UTC")


def localize_datetime(dt, user):
    """Convert datetime to user's local timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=pytz.UTC)
    user_tz = get_user_timezone(user)
    return dt.astimezone(user_tz)
