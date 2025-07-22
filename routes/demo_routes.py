import logging
import os
from datetime import UTC, datetime, timedelta
from logging import Logger

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    session,
    url_for,
)
from flask_login import login_user
from werkzeug import Response

from database import db
from models import (
    Account,
    Budget,
    Category,
    CategorySplit,
    Expense,
    RecurringExpense,
    User,
)
from services.defaults import create_default_categories
from services.helpers import reset_demo_data
from session_timeout import DemoTimeout

demo_bp = Blueprint("demo", __name__)


@demo_bp.route("/demo")
@demo_bp.route("/")
def login() -> str | Response:
    """Auto-login as demo user with session timeout."""
    logger: Logger = logging.getLogger(__name__)

    # Check if demo mode is enabled
    if os.getenv("DEMO_MODE", "False").lower() != "true":
        # If demo mode is not enabled, use default route
        return url_for("auth.home")

    # Get demo timeout instance
    demo_timeout: DemoTimeout | None = current_app.extensions.get(
        "demo_timeout"
    )
    if not demo_timeout:
        flash("DemoTimeout was not found in app extensions", "error")
        return url_for("auth.home")

    # Check concurrent session limit
    max_sessions = int(os.getenv("MAX_CONCURRENT_DEMO_SESSIONS", "5"))
    current_sessions: int = (
        demo_timeout.get_active_demo_sessions() if demo_timeout else 0
    )

    if current_sessions >= max_sessions:
        flash(
            f"Maximum number of demo sessions ({max_sessions}) has been "
            f"reached. Please try again later."
        )
        return redirect(url_for("demo.max_users"))

    # Find or create demo user
    demo_user: User | None = (
        db.select(User).filter_by(id="demo@example.com").first()
    )
    if not demo_user:
        logger.info("Creating new demo user")
        demo_user = User(
            id="demo@example.com", name="Demo User", is_admin=False
        )
        demo_user.set_password("demo")
        db.session.add(demo_user)
        db.session.commit()
    else:
        logger.info("Using existing demo user")

    # Try to register the demo session
    if not demo_timeout.register_demo_session(demo_user.id):
        flash(
            f"Maximum number of demo sessions ({max_sessions}) has been "
            f"reached. Please try again later."
        )
        return redirect(url_for("auth.login"))

    # Always reset demo data for each new session
    logger.info("Resetting demo data for new session")
    reset_demo_data(demo_user.id)

    # Create demo data
    result: bool = create_demo_data(demo_user.id)
    logger.info("Demo data creation result: %s", result)

    # Login as demo user
    login_user(demo_user)

    # Set demo start time and expiry time
    demo_timeout_minutes = int(os.getenv("DEMO_TIMEOUT_MINUTES", "10"))
    session["demo_start_time"] = datetime.now(UTC).timestamp()
    session["demo_expiry_time"] = (
        datetime.now(UTC) + timedelta(minutes=demo_timeout_minutes)
    ).timestamp()

    flash(
        f"Welcome to the demo! Your session will expire in "
        f"{demo_timeout_minutes} minutes."
    )

    return redirect(url_for("dashboard.dashboard"))


@demo_bp.route("/demo_max_users")
def demo_max_users() -> str:
    return render_template("demo/demo_concurrent.html")


@demo_bp.route("/demo_expired")
def demo_expired() -> str:
    """Handle expired demo sessions."""
    return render_template("demo/demo_expired.html")


@demo_bp.route("/demo-thanks")
def demo_thanks() -> str:
    """Page to thank users after demo session."""
    return render_template("demo/demo_thanks.html")


# Demo data creation function
def create_demo_data(user_id) -> bool:  # noqa: PLR0915
    """Create comprehensive sample data for demo users with proper checking."""
    logger: Logger = logging.getLogger(__name__)
    logger.info("Starting demo data creation for user %s", {user_id})

    # First create default categories
    create_default_categories(user_id)
    db.session.flush()

    # Check if demo data already exists
    existing_accounts: list[Account] = (
        db.select(Account).filter_by(user_id=user_id).all()
    )
    if existing_accounts:
        logger.info(
            "Found %d existing accounts for user %s",
            len(existing_accounts),
            user_id,
        )
        # We'll still continue to create any missing data

    # Create sample accounts if they don't exist
    checking: Account | None = (
        db.select(Account)
        .filter_by(name="Demo Checking", user_id=user_id)
        .first()
    )
    if not checking:
        logger.info("Creating demo checking account")
        checking = Account(
            name="Demo Checking",
            type="checking",
            institution="Demo Bank",
            balance=2543.87,
            user_id=user_id,
            currency_code="USD",
        )
        db.session.add(checking)

    savings: Account | None = (
        db.select(Account)
        .filter_by(name="Demo Savings", user_id=user_id)
        .first()
    )
    if not savings:
        logger.info("Creating demo savings account")
        savings = Account(
            name="Demo Savings",
            type="savings",
            institution="Demo Bank",
            balance=8750.25,
            user_id=user_id,
            currency_code="USD",
        )
        db.session.add(savings)

    credit: Account | None = (
        db.select(Account)
        .filter_by(name="Demo Credit Card", user_id=user_id)
        .first()
    )
    if not credit:
        logger.info("Creating demo credit card account")
        credit = Account(
            name="Demo Credit Card",
            type="credit",
            institution="Demo Bank",
            balance=-1250.30,
            user_id=user_id,
            currency_code="USD",
        )
        db.session.add(credit)

    investment: Account | None = (
        db.select(Account)
        .filter_by(name="Demo Investment", user_id=user_id)
        .first()
    )
    if not investment:
        logger.info("Creating demo investment account")
        investment = Account(
            name="Demo Investment",
            type="investment",
            institution="Vanguard",
            balance=15000.00,
            user_id=user_id,
            currency_code="USD",
        )
        db.session.add(investment)

    db.session.flush()

    # Get categories
    food_category: Category | None = (
        db.select(Category)
        .filter_by(name="Food", user_id=user_id)
        .first()
    )
    housing_category: Category | None = (
        db.select(Category)
        .filter_by(name="Housing", user_id=user_id)
        .first()
    )
    transportation_category: Category | None = (
        db.select(Category)
        .filter_by(name="Transportation", user_id=user_id)
        .first()
    )
    entertainment_category: Category | None = (
        db.select(Category)
        .filter_by(name="Entertainment", user_id=user_id)
        .first()
    )

    # Create sample transactions if they don't exist
    # 1. Regular expenses
    if (
        not db.select(Expense)
        .filter_by(description="Grocery shopping", user_id=user_id)
        .first()
    ):
        logger.info("Creating demo grocery expense")
        expense1 = Expense(
            description="Grocery shopping",
            amount=125.75,
            date=datetime.now(UTC),
            card_used="Demo Credit Card",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=food_category.id if food_category else None,
            split_with=None,
            group_id=None,
            account_id=credit.id,
            transaction_type="expense",
        )
        db.session.add(expense1)

    if (
        not db.select(Expense)
        .filter_by(description="Monthly rent", user_id=user_id)
        .first()
    ):
        logger.info("Creating demo rent expense")
        expense2 = Expense(
            description="Monthly rent",
            amount=1200.00,
            date=datetime.now(UTC) - timedelta(days=7),
            card_used="Demo Checking",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=housing_category.id if housing_category else None,
            split_with=None,
            group_id=None,
            account_id=checking.id,
            transaction_type="expense",
        )
        db.session.add(expense2)

    # 2. Income transactions
    if (
        not db.select(Expense)
        .filter_by(description="Salary deposit", user_id=user_id)
        .first()
    ):
        logger.info("Creating demo salary income")
        income1 = Expense(
            description="Salary deposit",
            amount=3500.00,
            date=datetime.now(UTC) - timedelta(days=15),
            card_used="Direct Deposit",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=None,
            split_with=None,
            group_id=None,
            account_id=checking.id,
            transaction_type="income",
        )
        db.session.add(income1)

    if (
        not db.select(Expense)
        .filter_by(description="Side gig payment", user_id=user_id)
        .first()
    ):
        logger.info("Creating demo side income")
        income2 = Expense(
            description="Side gig payment",
            amount=250.00,
            date=datetime.now(UTC) - timedelta(days=8),
            card_used="PayPal",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=None,
            split_with=None,
            group_id=None,
            account_id=checking.id,
            transaction_type="income",
        )
        db.session.add(income2)

    # Add expenses with category splits
    if (
        not db.select(Expense)
        .filter_by(description="Shopping trip (mixed)", user_id=user_id)
        .first()
    ):
        logger.info("Creating demo category split expense - shopping")

        # Get additional categories
        shopping_category: Category | None = (
            db.select(Category)
            .filter_by(name="Shopping", user_id=user_id)
            .first()
        )
        personal_category: Category | None = (
            db.select(Category)
            .filter_by(name="Personal", user_id=user_id)
            .first()
        )

        # Create the main expense with has_category_splits=True
        split_expense1 = Expense(
            description="Shopping trip (mixed)",
            amount=183.50,
            date=datetime.now(UTC) - timedelta(days=3),
            card_used="Demo Credit Card",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=None,  # No main category when using splits
            split_with=None,
            group_id=None,
            account_id=credit.id,
            transaction_type="expense",
            has_category_splits=True,  # This is the key flag
        )
        db.session.add(split_expense1)
        db.session.flush()  # Get the expense ID

        # Add category splits
        if shopping_category:
            cat_split1 = CategorySplit(
                expense_id=split_expense1.id,
                category_id=shopping_category.id,
                amount=120.00,
                description="Clothing items",
            )
            db.session.add(cat_split1)

        if food_category:
            cat_split2 = CategorySplit(
                expense_id=split_expense1.id,
                category_id=food_category.id,
                amount=38.50,
                description="Groceries",
            )
            db.session.add(cat_split2)

        if personal_category:
            cat_split3 = CategorySplit(
                expense_id=split_expense1.id,
                category_id=personal_category.id,
                amount=25.00,
                description="Personal care items",
            )
            db.session.add(cat_split3)

    # Add another split expense example - travel related
    if (
        not db.select(Expense)
        .filter_by(description="Weekend trip expenses", user_id=user_id)
        .first()
    ):
        logger.info("Creating demo category split expense - travel")

        # Create the main expense
        split_expense2 = Expense(
            description="Weekend trip expenses",
            amount=342.75,
            date=datetime.now(UTC) - timedelta(days=14),
            card_used="Demo Credit Card",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=None,
            split_with=None,
            group_id=None,
            account_id=credit.id,
            transaction_type="expense",
            has_category_splits=True,
        )
        db.session.add(split_expense2)
        db.session.flush()

        # Add category splits - assuming we have transportation and
        # entertainment categories
        if transportation_category:
            cat_split4 = CategorySplit(
                expense_id=split_expense2.id,
                category_id=transportation_category.id,
                amount=95.50,
                description="Gas and tolls",
            )
            db.session.add(cat_split4)

        if food_category:
            cat_split5 = CategorySplit(
                expense_id=split_expense2.id,
                category_id=food_category.id,
                amount=127.25,
                description="Dining out",
            )
            db.session.add(cat_split5)

        if entertainment_category:
            cat_split6 = CategorySplit(
                expense_id=split_expense2.id,
                category_id=entertainment_category.id,
                amount=120.00,
                description="Activities and entertainment",
            )
            db.session.add(cat_split6)

    # 3. Transfers between accounts
    if (
        not db.select(Expense)
        .filter_by(description="Transfer to savings", user_id=user_id)
        .first()
    ):
        logger.info("Creating demo transfer to savings")
        transfer1 = Expense(
            description="Transfer to savings",
            amount=500.00,
            date=datetime.now(UTC) - timedelta(days=10),
            card_used="Internal Transfer",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=None,
            split_with=None,
            group_id=None,
            account_id=checking.id,
            destination_account_id=savings.id,
            transaction_type="transfer",
        )
        db.session.add(transfer1)

    if (
        not db.select(Expense)
        .filter_by(description="Credit card payment", user_id=user_id)
        .first()
    ):
        logger.info("Creating demo credit card payment")
        transfer2 = Expense(
            description="Credit card payment",
            amount=750.00,
            date=datetime.now(UTC) - timedelta(days=12),
            card_used="Internal Transfer",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=None,
            split_with=None,
            group_id=None,
            account_id=checking.id,
            destination_account_id=credit.id,
            transaction_type="transfer",
        )
        db.session.add(transfer2)

    if (
        not db.select(Expense)
        .filter_by(description="Investment contribution", user_id=user_id)
        .first()
    ):
        logger.info("Creating demo investment transfer")
        transfer3 = Expense(
            description="Investment contribution",
            amount=1000.00,
            date=datetime.now(UTC) - timedelta(days=20),
            card_used="Internal Transfer",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=None,
            split_with=None,
            group_id=None,
            account_id=checking.id,
            destination_account_id=investment.id,
            transaction_type="transfer",
        )
        db.session.add(transfer3)

    # 4. Create recurring expenses
    netflix_recurring: RecurringExpense | None = (
        db.select(RecurringExpense)
        .filter_by(description="Netflix Subscription", user_id=user_id)
        .first()
    )
    if not netflix_recurring:
        logger.info("Creating demo Netflix recurring expense")
        recurring1 = RecurringExpense(
            description="Netflix Subscription",
            amount=14.99,
            card_used="Demo Credit Card",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=entertainment_category.id
            if entertainment_category
            else None,
            frequency="monthly",
            start_date=datetime.now(UTC) - timedelta(days=30),
            active=True,
            account_id=credit.id,
        )
        db.session.add(recurring1)

    rent_recurring: RecurringExpense | None = (
        db.select(RecurringExpense)
        .filter_by(description="Monthly Rent Payment", user_id=user_id)
        .first()
    )
    if not rent_recurring:
        logger.info("Creating demo rent recurring expense")
        recurring2 = RecurringExpense(
            description="Monthly Rent Payment",
            amount=1200.00,
            card_used="Demo Checking",
            split_method="equal",
            paid_by=user_id,
            user_id=user_id,
            category_id=housing_category.id if housing_category else None,
            frequency="monthly",
            start_date=datetime.now(UTC) - timedelta(days=15),
            active=True,
            account_id=checking.id,
        )
        db.session.add(recurring2)

    # 5. Create budgets
    food_budget: Budget | None = (
        db.select(Budget)
        .filter_by(name="Monthly Food", user_id=user_id)
        .first()
    )
    if food_category and not food_budget:
        logger.info("Creating demo food budget")
        budget1 = Budget(
            user_id=user_id,
            category_id=food_category.id,
            name="Monthly Food",
            amount=600.00,
            period="monthly",
            include_subcategories=True,
            start_date=datetime.now(UTC).replace(day=1),
            is_recurring=True,
            active=True,
        )
        db.session.add(budget1)

    transport_budget: Budget | None = (
        db.select(Budget)
        .filter_by(name="Transportation Budget", user_id=user_id)
        .first()
    )
    if transportation_category and not transport_budget:
        logger.info("Creating demo transportation budget")
        budget2 = Budget(
            user_id=user_id,
            category_id=transportation_category.id,
            name="Transportation Budget",
            amount=300.00,
            period="monthly",
            include_subcategories=True,
            start_date=datetime.now(UTC).replace(day=1),
            is_recurring=True,
            active=True,
        )
        db.session.add(budget2)

    entertainment_budget = (
        db.select(Budget)
        .filter_by(name="Entertainment Budget", user_id=user_id)
        .first()
    )
    if entertainment_category and not entertainment_budget:
        logger.info("Creating demo entertainment budget")
        budget3 = Budget(
            user_id=user_id,
            category_id=entertainment_category.id,
            name="Entertainment Budget",
            amount=200.00,
            period="monthly",
            include_subcategories=True,
            start_date=datetime.now(UTC).replace(day=1),
            is_recurring=True,
            active=True,
        )
        db.session.add(budget3)

    # Commit all changes
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Error creating demo data")
        return False
    else:
        logger.info("Demo data created/updated successfully")
        return True
