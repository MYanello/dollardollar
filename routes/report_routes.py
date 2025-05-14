import calendar
from datetime import datetime

from flask import Blueprint, current_app, flash, render_template, request
from flask_login import current_user

from models import User
from services.reports import send_monthly_report
from services.wrappers import login_required_dev

report_bp = Blueprint("report", __name__)


@report_bp.route("/generate_monthly_report", methods=["GET", "POST"])
@login_required_dev
def generate_monthly_report():
    """Generate and send a monthly expense report for the current user."""
    if request.method == "POST":
        try:
            report_date = datetime.strptime(
                request.form.get("report_month", ""), "%Y-%m"
            )
            report_year = report_date.year
            report_month = report_date.month
        except ValueError:
            # Default to previous month if invalid input
            today = datetime.now()
            if today.month == 1:
                report_month = 12
                report_year = today.year - 1
            else:
                report_month = today.month - 1
                report_year = today.year

        # Generate and send the report
        success = send_monthly_report(
            current_user.id, report_year, report_month
        )

        if success:
            flash("Monthly report has been sent to your email.")
        else:
            flash("Error generating monthly report. Please try again later.")

    # For GET request, show the form
    # Get the last 12 months for selection
    months = []
    today = datetime.now()
    for i in range(12):
        if today.month - i <= 0:
            month = today.month - i + 12
            year = today.year - 1
        else:
            month = today.month - i
            year = today.year

        month_name = calendar.month_name[month]
        months.append(
            {"value": f"{year}-{month:02d}", "label": f"{month_name} {year}"}
        )

    return render_template("generate_report.html", months=months)


def send_automatic_monthly_reports():
    """Send monthly reports to all users who have opted in."""
    with current_app.app_context():
        # Get the previous month
        today = datetime.now()
        if today.month == 1:
            report_month = 12
            report_year = today.year - 1
        else:
            report_month = today.month - 1
            report_year = today.year

        # Get users who have opted in
        # (you'd need to add this field to User model)
        # For now, we'll assume all users want reports
        users = User.query.all()

        current_app.logger.info(
            f"Starting to send monthly reports for {calendar.month_name[report_month]} {report_year}"
        )

        success_count = 0
        for user in users:
            if send_monthly_report(user.id, report_year, report_month):
                success_count += 1

        current_app.logger.info(
            f"Sent {success_count}/{len(users)} monthly reports"
        )
