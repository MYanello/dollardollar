from datetime import UTC, datetime, timedelta
from typing import Any

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug import Response

from database import db
from extensions import gocardless_client as client
from models import GoCardlessSettings
from models.account import Account
from models.currency import Currency
from models.expense import Expense
from models.gocardless import Agreement, Requisition
from util import (
    auto_categorize_transaction,
    detect_internal_transfer,
    get_category_id,
)

gocardless_bp = Blueprint(
    "gocardless",
    __name__,
    template_folder="templates",
    url_prefix="/gocardless",
)


# --------------------
# ROUTES: GoCardless
# --------------------
@gocardless_bp.route("/connect")
@login_required
def connect() -> Response:
    """Redirect users to GoCardless site to get their setup token."""
    if not current_app.config["GOCARDLESS_ENABLED"]:
        flash("GoCardless is not enabled. Please contact the administrator.")
        return redirect(url_for("account.advanced") + "#gocardless")

    # Get the URL for users to obtain their secret tokens
    setup_token_url: str = client.get_secret_token_url()

    # Redirect to GoCardless setup token page
    return redirect(setup_token_url)


@gocardless_bp.route("/process_token", methods=["POST"])
@login_required
def process_token() -> Response:
    """Process the setup token provided by the user."""
    current_app.logger.info("RUNNING PROCESS_TOKEN")
    if not current_app.config["GOCARDLESS_ENABLED"]:
        flash("GoCardless is not enabled.", "error")
        return redirect(url_for("account.advanced") + "#gocardless")

    secret_id: str | None = request.form.get("secret_id")
    secret_key: str | None = request.form.get("secret_key")

    if not secret_id or not secret_key:
        flash("You must provide both secret tokens.", "error")
        return redirect(url_for("account.advanced") + "#gocardless")

    # Fetch access and refresh tokens with secret id & secret key
    tokens: dict | None = client.get_tokens(secret_id, secret_key)
    if not tokens:
        flash("Invalid tokens. Please try again.", "error")
        return redirect(url_for("account.advanced") + "#gocardless")

    # # Create a GoCardless settings record for this user
    try:
        # Check if a GoCardless settings record already exists for this user
        gocardless_settings: GoCardlessSettings | None = (
            db.session.query(GoCardlessSettings)
            .filter_by(user_id=current_user.id)
            .first()
        )

        # Usually 30 days after the token was obtained
        refresh_expiration: datetime = datetime.now(UTC) + timedelta(
            seconds=int(tokens["refresh_expires"])
        )

        # Usually 24 hours after the token was obtained
        access_expiration: datetime = datetime.now(UTC) + timedelta(
            seconds=int(tokens["access_expires"])
        )

        if gocardless_settings:
            # Update existing settings
            gocardless_settings.access_token = tokens["access"]
            gocardless_settings.access_token_expiration = access_expiration

            gocardless_settings.refresh_token = tokens["refresh"]
            gocardless_settings.refresh_token_expiration = refresh_expiration

            gocardless_settings.last_sync = None  # Reset last sync time
            gocardless_settings.enabled = True
            gocardless_settings.sync_frequency = "daily"  # Default to daily
        else:
            # Create new settings
            gocardless_settings = GoCardlessSettings(
                user_id="dev@example.com",
                access_token=tokens["access"],
                access_token_expiration=access_expiration,
                refresh_token=tokens["refresh"],
                refresh_token_expiration=refresh_expiration,
                last_sync=None,
                enabled=True,
                sync_frequency="daily",
            )
            db.session.add(gocardless_settings)

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error saving GoCardless settings")
        flash(f"Error saving GoCardless settings: {e!s}")
        return redirect(url_for("account.advanced"))

    flash("Tokens successfully obtained.")
    return redirect(url_for("gocardless.choose_bank"))


@gocardless_bp.route("/choose-bank")
@login_required
def choose_bank() -> Response | str:
    """Render form to choose a bank from."""
    if not current_app.config["GOCARDLESS_ENABLED"]:
        flash("GoCardless is not enabled.", "error")
        return redirect(url_for("account.advanced"))
    settings: GoCardlessSettings | None = (
        db.session.query(GoCardlessSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not settings or not settings.access_token:
        flash("No GoCardless connection found.", "error")
        return redirect(url_for("account.advanced"))

    banks: dict[str, list[dict[str, Any]]] | None = client.get_banks_by_country(
        settings.access_token
    )
    countries: list[list[str]] = client.get_countries()
    if not banks:
        flash("Could not retrieve list of banks from GoCardless.", "error")
        return redirect(url_for("account.advanced"))

    countries.append(["Development", "DEV"])
    banks["DEV"] = [
        {
            "id": "SANDBOXFINANCE_SFIN0000",
            "name": "Sandbox",
            "countries": ["DEV"],
            "max_access_valid_for_days": "90",
        }
    ]
    return render_template(
        "gocardless/choose_bank.html",
        countries=countries,
        banks=banks,
    )


@gocardless_bp.route("/process_bank", methods=["POST"])
@login_required
def process_bank() -> Response | str:
    """Create user agreement and redirect user to accept it."""
    if not current_app.config["GOCARDLESS_ENABLED"]:
        flash("GoCardless is not enabled.", "error")
        return redirect(url_for("account.advanced"))
    settings: GoCardlessSettings | None = (
        db.session.query(GoCardlessSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not settings or not settings.access_token:
        flash("No GoCardless connection found.", "error")
        return redirect(url_for("account.advanced"))

    access_scope: list[str] = request.form.getlist("access_scope")
    access_valid: str = request.form.get("access_valid_for_days", "90")
    historical_days: str = request.form.get("max_historical_days", "90")
    bank: str | None = request.form.get("bank")
    if not bank:
        flash("You need to select a bank to continue.")
        return redirect(url_for("account.advanced"))

    agreement: dict[str, Any] | None = client.create_user_agreement(
        settings.access_token, bank, historical_days, access_valid, access_scope
    )
    if not agreement or "id" not in agreement:
        flash("Something went wrong while creating the user agreement.")
        return redirect(url_for("account.advanced"))

    requisition: dict[str, Any] | None = client.create_requisition(
        settings.access_token,
        bank,
        url_for(".list_accounts", _external=True),
        agreement["id"],
    )
    if not requisition or "link" not in requisition:
        flash("Something went wrong while creating the requisition.")
        return redirect(url_for("account.advanced"))

    agr = Agreement(
        agreement["id"],
        current_user.id,
        bank,
        int(historical_days),
        int(access_valid),
        "balances" in access_scope,
        "details" in access_scope,
        "transactions" in access_scope,
        datetime.now(UTC) + timedelta(days=int(access_valid)),
    )
    req = Requisition(requisition["id"], current_user.id, bank, agreement["id"])
    db.session.add(agr)
    db.session.add(req)
    db.session.commit()
    return redirect(requisition["link"])


@gocardless_bp.route("/list_accounts")
@login_required
def list_accounts() -> Response | str:
    """List available accounts for GoCardless."""
    if not current_app.config["GOCARDLESS_ENABLED"]:
        flash("GoCardless is not enabled.", "error")
        return redirect(url_for("account.advanced"))
    settings: GoCardlessSettings | None = (
        db.session.query(GoCardlessSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not settings or not settings.access_token:
        flash("No GoCardless connection found.", "error")
        return redirect(url_for("account.advanced"))
    requisitions: list[Requisition] = (
        db.session.query(Requisition)
        .filter_by(user_id=current_user.id)
        .join(Requisition.agreement)
        .order_by(Agreement.created_at.desc())
        .all()
    )
    accounts: dict[str, Any] | None = client.get_accounts(
        settings.access_token, requisitions[0].id
    )
    if not accounts:
        flash("Something went wrong while retrieving accounts.", "error")
        return redirect(url_for("account.advanced"))

    account_details: dict[str, Any] = {}
    for account in accounts["accounts"]:
        account_details[account] = client.get_basic_account_information(
            settings.access_token, account
        )
    session["account_institution_ids"] = {
        x: account_details[x]["institution_id"] for x in accounts["accounts"]
    }

    return render_template(
        "gocardless/accounts.html",
        accounts=account_details,
        requisitions=requisitions,
    )


@gocardless_bp.route("/customize_accounts", methods=["POST"])
@login_required
def customize_accounts() -> Response | str:
    """Display page to customize previously selected accounts."""
    if not current_app.config["GOCARDLESS_ENABLED"]:
        flash("GoCardless is not enabled.", "error")
        return redirect(url_for("account.advanced"))
    settings: GoCardlessSettings | None = (
        db.session.query(GoCardlessSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not settings or not settings.access_token:
        flash("No GoCardless connection found.", "error")
        return redirect(url_for("account.advanced"))
    accounts: list[str] = request.form.getlist("account")
    session["gocardless_accounts"] = accounts
    if not accounts:
        flash("No accounts were selected.", "error")
        return redirect(url_for("account.advanced"))

    currencies: list[Currency] = db.session.query(Currency).all()
    institutions = {}
    account_details: dict[str, Any] = {}
    for account in accounts:
        account_details[account] = client.get_account_details(
            settings.access_token, account
        )
        inst_id: str = session["account_institution_ids"][account]
        account_details[account]["institution_id"] = inst_id
        if inst_id not in institutions:
            institutions[inst_id] = client.get_institution_info(
                settings.access_token, inst_id
            )
    return render_template(
        "gocardless/customize_accounts.html",
        accounts=accounts,
        currencies=currencies,
        institutions=institutions,
        account_details=account_details,
    )


@gocardless_bp.route("/add_accounts", methods=["POST"])
@login_required
def add_accounts() -> Response | str:
    """Add selected accounts and their customizations."""
    if not current_app.config["GOCARDLESS_ENABLED"]:
        flash("GoCardless is not enabled.", "error")
        return redirect(url_for("account.advanced"))
    settings: GoCardlessSettings | None = (
        db.session.query(GoCardlessSettings)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if not settings or not settings.access_token:
        flash("No GoCardless connection found.", "error")
        return redirect(url_for("account.advanced"))

    accounts: list[str] = session["gocardless_accounts"]
    details: dict[str, Any] = {}
    acc_num: int = 0
    transactions_num: int = 0
    for account in accounts:
        institution: str | None = request.form.get(f"institution_{account}")
        name: str | None = request.form.get(f"name_{account}")
        acc_type: str | None = request.form.get(f"type_{account}")
        currency: str | None = request.form.get(f"currency_code_{account}")

        if not institution or not name or not acc_type or not currency:
            current_app.logger.info(
                "Skipped adding account %s, due to missing information.",
                account,
            )
            continue

        account_details: dict[str, Any] | None = client.get_account_details(
            settings.access_token, account
        )
        if not account_details:
            continue
        details[account] = account_details["account"]
        details[account]["institution"] = institution
        details[account]["name"] = name
        details[account]["currency"] = currency
        details[account]["type"] = acc_type

        balances: dict[str, Any] | None = client.get_account_balances(
            settings.access_token, account
        )
        all_transactions: dict[str, Any] | None = (
            client.get_account_transactions(settings.access_token, account)
        )

        if not balances or not all_transactions:
            flash(
                "Something went wrong while fetching account information",
                "error",
            )
            return redirect(url_for("account.advanced"))

        balance: dict[str, Any] = balances["balances"][0]
        booked_transactions: list[dict[str, Any]] = all_transactions[
            "transactions"
        ]["booked"]
        current_app.logger.info(all_transactions)
        existing_account: Account | None = (
            db.session.query(Account)
            .filter_by(
                user_id=current_user.id,
                name=name,
                institution=institution,
            )
            .first()
        )

        if existing_account:
            existing_account.balance = 0.0
            existing_account.last_sync = datetime.now(UTC)
            existing_account.currency_code = currency
            existing_account.import_source = "gocardless"
            existing_account.type = acc_type
            db.session.commit()
        else:
            new_account = Account(
                name=name,
                type=acc_type,
                institution=institution,
                user_id=current_user.id,
                currency_code=currency,
                last_sync=datetime.now(UTC),
                import_source="gocardless",
                external_id=details[account]["iban"],
                status="active",
                balance=float(balance["balanceAmount"]["amount"]),
            )
            db.session.add(new_account)
            db.session.commit()
            acc_num += 1

        transactions: list[Expense]
        transactions, _ = client.create_transactions(
            booked_transactions,
            existing_account or new_account,
            detect_internal_transfer,  # Your transfer detection function
            auto_categorize_transaction,  # Your auto-categorization function
            get_category_id,  # Your function to find/create categories
        )
        for transaction in transactions:
            existing: Expense | None = (
                db.session.query(Expense)
                .filter_by(
                    user_id=current_user.id,
                    external_id=transaction.external_id,
                    import_source="gocardless",
                )
                .first()
            )

            if not existing:
                db.session.add(transaction)
                transactions_num += 1
            db.session.commit()

    flash(
        f"Added {acc_num} accounts and {transactions_num} transactions"
        "from GoCardless."
    )

    return redirect(url_for("account.accounts"))
