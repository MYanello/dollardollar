import os
from flask import (
    Flask,
    request,
    jsonify,

)
from flask_login import (
    current_user,
)
from services.defaults import create_default_budgets, create_default_categories
import logging
import ssl
from oidc_auth import setup_oidc_config, register_oidc_routes
from oidc_user import extend_user_model
from simplefin_client import SimpleFin
import base64
import pytz
from config import get_config
from extensions import login_manager, mail, migrate, scheduler, init_db
import re
import json
from datetime import datetime, timedelta
from database import db
from services.helpers import init_default_currencies
from sqlalchemy import func, or_, and_, inspect, text

from routes import register_blueprints
from session_timeout import DemoTimeout

from models import Account, Budget, Category, CategoryMapping, CategorySplit, Currency, Expense, Group, IgnoredRecurringPattern, RecurringExpense, Settlement, Tag, User
from tables import group_users

from util import auto_categorize_transaction, check_db_structure, detect_internal_transfer, get_category_id

# Development user credentials from environment
DEV_USER_EMAIL = os.getenv('DEV_USER_EMAIL', 'dev@example.com')
DEV_USER_PASSWORD = os.getenv('DEV_USER_PASSWORD', 'dev')
os.environ["OPENSSL_LEGACY_PROVIDER"] = "1"

APP_VERSION = "4.2"

try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass

def create_app(config_object=None):
    # App Factory
    app = Flask(__name__)

    if config_object is None:
        config_object = get_config()
    app.config.from_object(config_object)
    register_blueprints(app)
    # Initialize extensions with the app
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    mail.init_app(app)
    scheduler.init_app(app)

    check_db_structure(app)

    return app

app = create_app()
logging.basicConfig(level=getattr(logging, app.config['LOG_LEVEL']))
app.config['SIMPLEFIN_ENABLED'] = os.getenv('SIMPLEFIN_ENABLED', 'True').lower() == 'true'
app.config['SIMPLEFIN_SETUP_TOKEN_URL'] = os.getenv('SIMPLEFIN_SETUP_TOKEN_URL', 'https://beta-bridge.simplefin.org/setup-token')

app.config['GOCARDLESS_ENABLED'] = os.getenv('GOCARDLESS_ENABLED', 'True').lower() == 'true'

# Email configuration from environment variables
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.getenv('MAIL_USE_SSL', 'False').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', os.getenv('MAIL_USERNAME'))

app.config['TIMEZONE'] = 'EST'  # Default timezone

# Initialize scheduler
# scheduler = APScheduler()
scheduler.timezone = pytz.timezone('EST') # Explicitly set scheduler to use EST
scheduler.init_app(app)
logging.basicConfig(level=getattr(logging, app.config['LOG_LEVEL']))
@scheduler.task('cron', id='monthly_reports', day=1, hour=1, minute=0)
def scheduled_monthly_reports():
    """Run on the 1st day of each month at 1:00 AM"""
    send_automatic_monthly_reports()
@scheduler.task('cron', id='simplefin_sync', hour=23, minute=0)
def scheduled_simplefin_sync():
    """Run every day at 11:00 PM"""
    sync_all_simplefin_accounts()
scheduler.start()

simplefin_client = SimpleFin(app)

oidc_enabled = setup_oidc_config(app)
# db = SQLAlchemy(app)
# login_manager = LoginManager()
# login_manager.init_app(app)
# login_manager.login_view = 'login'


# migrate = Migrate(app, db)


# Initialize demo timeout middleware
demo_timeout = DemoTimeout(
    timeout_minutes=int(os.getenv('DEMO_TIMEOUT_MINUTES', 10)),
    demo_users=[
        'demo@example.com',
        'demo1@example.com',
        'demo2@example.com',
        # Add any specific demo accounts here
    ]
)
demo_timeout.init_app(app)
app.extensions['demo_timeout'] = demo_timeout  # Store for access in decorator

if oidc_enabled:
    User = extend_user_model(db, User)

@login_manager.user_loader
def load_user(id):
    return User.query.filter_by(id=id).first()

def init_db():
    """Initialize the database"""
    with app.app_context():
        db.drop_all()  # This will drop all existing tables
        db.create_all()  # This will create new tables with current schema
        print("Database initialized successfully!")

        # Create dev user in development mode
        if app.config['DEVELOPMENT_MODE']:
            dev_user = User(
                id=DEV_USER_EMAIL,
                name='Developer',
                is_admin=True
            )
            dev_user.set_password(DEV_USER_PASSWORD)
            db.session.add(dev_user)
            db.session.commit()
            create_default_categories(dev_user.id)
            create_default_budgets(dev_user.id)
            print("Development user created:", DEV_USER_EMAIL)

#--------------------
# BUSINESS LOGIC FUNCTIONS
#--------------------

# enhance transfer detection
def calculate_asset_debt_trends(current_user):
    """
    Calculate asset and debt trends for a user's accounts
    """
    from datetime import datetime, timedelta

    # Initialize tracking
    monthly_assets = {}
    monthly_debts = {}

    # Get today's date and calculate a reasonable historical range (last 12 months)
    today = datetime.now()
    twelve_months_ago = today - timedelta(days=365)

    # Get all accounts for the user
    accounts = Account.query.filter_by(user_id=current_user.id).all()

    # Get user's preferred currency code
    user_currency_code = current_user.default_currency_code or 'USD'

    # Calculate true total assets and debts directly from accounts (for accurate current total)
    direct_total_assets = 0
    direct_total_debts = 0

    for account in accounts:
        # Get account's currency code, default to user's preferred currency
        account_currency_code = account.currency_code or user_currency_code

        # Convert account balance to user's currency if needed
        if account_currency_code != user_currency_code:
            converted_balance = convert_currency(account.balance, account_currency_code, user_currency_code)
        else:
            converted_balance = account.balance

        if account.type in ['checking', 'savings', 'investment'] and converted_balance > 0:
            direct_total_assets += converted_balance
        elif account.type in ['credit'] or converted_balance < 0:
            # For credit cards with negative balances (standard convention)
            direct_total_debts += abs(converted_balance)

    # Process each account for historical trends
    for account in accounts:
        # Get account's currency code, default to user's preferred currency
        account_currency_code = account.currency_code or user_currency_code

        # Categorize account types
        is_asset = account.type in ['checking', 'savings', 'investment'] and account.balance > 0
        is_debt = account.type in ['credit'] or account.balance < 0

        # Skip accounts with zero or near-zero balance
        if abs(account.balance or 0) < 0.01:
            continue

        # Get monthly transactions for this account
        transactions = Expense.query.filter(
            Expense.account_id == account.id,
            Expense.user_id == current_user.id,
            Expense.date >= twelve_months_ago
        ).order_by(Expense.date).all()

        # Track balance over time
        balance_history = {}
        current_balance = account.balance or 0

        # Start with the most recent balance
        balance_history[today.strftime('%Y-%m')] = current_balance

        # Process transactions to track historical balances
        for transaction in transactions:
            month_key = transaction.date.strftime('%Y-%m')

            # Consider currency conversion for each transaction if needed
            transaction_amount = transaction.amount
            if transaction.currency_code and transaction.currency_code != account_currency_code:
                transaction_amount = convert_currency(transaction_amount, transaction.currency_code, account_currency_code)

            # Adjust balance based on transaction
            if transaction.transaction_type == 'income':
                current_balance += transaction_amount
            elif transaction.transaction_type == 'expense' or transaction.transaction_type == 'transfer':
                current_balance -= transaction_amount

            # Update monthly balance
            balance_history[month_key] = current_balance

        # Convert balance history to user currency if needed
        if account_currency_code != user_currency_code:
            for month, balance in balance_history.items():
                balance_history[month] = convert_currency(balance, account_currency_code, user_currency_code)

        # Categorize and store balances
        for month, balance in balance_history.items():
            if is_asset:
                # For asset accounts, add positive balances to the monthly total
                monthly_assets[month] = monthly_assets.get(month, 0) + balance
            elif is_debt:
                # For debt accounts or negative balances, add the absolute value to the debt total
                monthly_debts[month] = monthly_debts.get(month, 0) + abs(balance)

    # Ensure consistent months across both series
    all_months = sorted(set(list(monthly_assets.keys()) + list(monthly_debts.keys())))

    # Fill in missing months with previous values or zero
    assets_trend = []
    debts_trend = []

    for month in all_months:
        assets_trend.append(monthly_assets.get(month, assets_trend[-1] if assets_trend else 0))
        debts_trend.append(monthly_debts.get(month, debts_trend[-1] if debts_trend else 0))

    # Use the directly calculated totals rather than the trend values for accuracy
    total_assets = direct_total_assets
    total_debts = direct_total_debts
    net_worth = total_assets - total_debts

    return {
        'months': all_months,
        'assets': assets_trend,
        'debts': debts_trend,
        'total_assets': total_assets,
        'total_debts': total_debts,
        'net_worth': net_worth
    }


# Update the determine_transaction_type function to detect internal transfers
def determine_transaction_type(row, current_account_id=None):
    """
    Determine transaction type based on row data from CSV import
    Now with enhanced internal transfer detection
    """
    type_column = request.form.get('type_column')
    negative_is_expense = 'negative_is_expense' in request.form

    # Get description column name (default to 'Description')
    description_column = request.form.get('description_column', 'Description')
    description = row.get(description_column, '').strip()

    # Get amount column name (default to 'Amount')
    amount_column = request.form.get('amount_column', 'Amount')
    amount_str = row.get(amount_column, '0').strip().replace('$', '').replace(',', '')

    try:
        amount = float(amount_str)
    except ValueError:
        amount = 0

    # First check for internal transfer
    if current_account_id:
        is_transfer, _, _ = detect_internal_transfer(description, amount, current_account_id)
        if is_transfer:
            return 'transfer'

    # Check if there's a specific transaction type column
    if type_column and type_column in row:
        type_value = row[type_column].strip().lower()

        # Map common terms to transaction types
        if type_value in ['expense', 'debit', 'purchase', 'payment', 'withdrawal']:
            return 'expense'
        elif type_value in ['income', 'credit', 'deposit', 'refund']:
            return 'income'
        elif type_value in ['transfer', 'move', 'xfer']:
            return 'transfer'

    # If no type column or unknown value, try to determine from description
    if description:
        # Common transfer keywords
        transfer_keywords = ['transfer', 'xfer', 'move', 'moved to', 'sent to', 'to account', 'between accounts']
        # Common income keywords
        income_keywords = ['salary', 'deposit', 'refund', 'interest', 'dividend', 'payment received']
        # Common expense keywords
        expense_keywords = ['payment', 'purchase', 'fee', 'subscription', 'bill']

        desc_lower = description.lower()

        # Check for keywords in description
        if any(keyword in desc_lower for keyword in transfer_keywords):
            return 'transfer'
        elif any(keyword in desc_lower for keyword in income_keywords):
            return 'income'
        elif any(keyword in desc_lower for keyword in expense_keywords):
            return 'expense'

    # If still undetermined, use amount sign
    try:
        # Determine type based on amount sign and settings
        if amount < 0 and negative_is_expense:
            return 'expense'
        elif amount > 0 and negative_is_expense:
            return 'income'
        elif amount < 0 and not negative_is_expense:
            return 'income'  # In some systems, negative means money coming in
        else:
            return 'expense'  # Default to expense for positive amounts
    except ValueError:
        # If amount can't be parsed, default to expense
        return 'expense'



def update_category_mappings(transaction_id, category_id, learn=False):
    """
    Update category mappings based on a manually categorized transaction
    If learn=True, create a new mapping based on this categorization
    """
    transaction = Expense.query.get(transaction_id)
    if not transaction or not category_id:
        return False

    if learn:
        # Extract a good keyword from the description
        keyword = extract_keywords(transaction.description)

        # Check if a similar mapping already exists
        existing = CategoryMapping.query.filter_by(
            user_id=transaction.user_id,
            keyword=keyword,
            active=True
        ).first()

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
                match_count=1
            )
            db.session.add(new_mapping)
            db.session.commit()

        return True

    return False

def extract_keywords(description):
    """
    Extract meaningful keywords from a transaction description
    Returns the most significant word or phrase
    """
    if not description:
        return ""

    # Clean up description
    clean_desc = description.strip().lower()

    # Split into words
    words = clean_desc.split()

    # Remove common words that aren't useful for categorization
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'on', 'in', 'with', 'for', 'to', 'from', 'by', 'at', 'of'}
    filtered_words = [w for w in words if w not in stop_words and len(w) > 2]

    if not filtered_words:
        # If no good words remain, use the longest word from the original
        return max(words, key=len) if words else ""

    # Use the longest remaining word as the keyword
    # This is a simple approach - could be improved with more sophisticated NLP
    return max(filtered_words, key=len)


def create_default_category_mappings(user_id):
    """Create default category mappings for a new user"""
    # Check if user already has any mappings
    existing_mappings_count = CategoryMapping.query.filter_by(user_id=user_id).count()

    # Only create defaults if user has no mappings
    if existing_mappings_count > 0:
        return

    # Get user's categories to map to
    # We'll need to find the appropriate category IDs for the current user
    categories = {}

    # Find common top-level categories
    for category_name in ["Food", "Transportation", "Housing", "Shopping", "Entertainment", "Health", "Personal", "Other"]:
        category = Category.query.filter_by(
            user_id=user_id,
            name=category_name,
            parent_id=None
        ).first()

        if category:
            categories[category_name.lower()] = category.id

            # Also get subcategories
            for subcategory in category.subcategories:
                categories[subcategory.name.lower()] = subcategory.id

    # If we couldn't find any categories, we can't create mappings
    if not categories:
        app.logger.warning(f"Could not create default category mappings for user {user_id}: no categories found")
        return

    # Default mappings as (keyword, category_key, is_regex, priority)
    default_mappings = [
        # Food & Dining
        ("grocery", "groceries", False, 5),
        ("groceries", "groceries", False, 5),
        ("supermarket", "groceries", False, 5),
        ("walmart", "groceries", False, 3),
        ("target", "groceries", False, 3),
        ("costco", "groceries", False, 5),
        ("safeway", "groceries", False, 5),
        ("kroger", "groceries", False, 5),
        ("aldi", "groceries", False, 5),
        ("trader joe", "groceries", False, 5),
        ("whole foods", "groceries", False, 5),
        ("wegmans", "groceries", False, 5),
        ("publix", "groceries", False, 5),
        ("sprouts", "groceries", False, 5),
        ("sams club", "groceries", False, 5),

        # Restaurants
        ("restaurant", "restaurants", False, 5),
        ("dining", "restaurants", False, 5),
        ("takeout", "restaurants", False, 5),
        ("doordash", "restaurants", False, 5),
        ("ubereats", "restaurants", False, 5),
        ("grubhub", "restaurants", False, 5),
        ("mcdonald", "restaurants", False, 5),
        ("burger", "restaurants", False, 4),
        ("pizza", "restaurants", False, 4),
        ("chipotle", "restaurants", False, 5),
        ("panera", "restaurants", False, 5),
        ("kfc", "restaurants", False, 5),
        ("wendy's", "restaurants", False, 5),
        ("taco bell", "restaurants", False, 5),
        ("chick-fil-a", "restaurants", False, 5),
        ("five guys", "restaurants", False, 5),
        ("ihop", "restaurants", False, 5),
        ("denny's", "restaurants", False, 5),

        # Coffee shops
        ("starbucks", "coffee shops", False, 5),
        ("coffee", "coffee shops", False, 4),
        ("dunkin", "coffee shops", False, 5),
        ("peet", "coffee shops", False, 5),
        ("tim hortons", "coffee shops", False, 5),

        # Gas & Transportation
        ("gas station", "gas", False, 5),
        ("gasoline", "gas", False, 5),
        ("fuel", "gas", False, 5),
        ("chevron", "gas", False, 5),
        ("shell", "gas", False, 5),
        ("exxon", "gas", False, 5),
        ("tesla supercharger", "gas", False, 5),
        ("ev charging", "gas", False, 5),

        # Rideshare & Transit
        ("uber", "rideshare", False, 5),
        ("lyft", "rideshare", False, 5),
        ("taxi", "rideshare", False, 5),
        ("transit", "public transit", False, 5),
        ("subway", "public transit", False, 5),
        ("bus", "public transit", False, 5),
        ("train", "public transit", False, 5),
        ("amtrak", "public transit", False, 5),
        ("greyhound", "public transit", False, 5),
        ("parking", "transportation", False, 5),
        ("toll", "transportation", False, 5),
        ("bike share", "transportation", False, 5),
        ("scooter rental", "transportation", False, 5),

        # Housing & Utilities
        ("rent", "rent/mortgage", False, 5),
        ("mortgage", "rent/mortgage", False, 5),
        ("airbnb", "rent/mortgage", False, 5),
        ("vrbo", "rent/mortgage", False, 5),
        ("water bill", "utilities", False, 5),
        ("electric", "utilities", False, 5),
        ("utility", "utilities", False, 5),
        ("utilities", "utilities", False, 5),
        ("internet", "utilities", False, 5),
        ("Ngrid", "utilities", False, 5),
        ("maintenance", "home maintenance", False, 4),
        ("repair", "home maintenance", False, 4),
        ("hvac", "home maintenance", False, 5),
        ("pest control", "home maintenance", False, 5),
        ("home security", "home maintenance", False, 5),
        ("home depot", "home maintenance", False, 5),
        ("lowe's", "home maintenance", False, 5),

        # Shopping
        ("amazon", "shopping", False, 5),
        ("ebay", "shopping", False, 5),
        ("etsy", "shopping", False, 5),
        ("clothing", "clothing", False, 5),
        ("apparel", "clothing", False, 5),
        ("shoes", "clothing", False, 5),
        ("electronics", "electronics", False, 5),
        ("best buy", "electronics", False, 5),
        ("apple", "electronics", False, 5),
        ("microsoft", "electronics", False, 5),
        ("furniture", "shopping", False, 5),
        ("homegoods", "shopping", False, 5),
        ("ikea", "shopping", False, 5),
        ("tj maxx", "shopping", False, 5),
        ("marshalls", "shopping", False, 5),
        ("nordstrom", "shopping", False, 5),
        ("macys", "shopping", False, 5),
        ("zara", "shopping", False, 5),
        ("uniqlo", "shopping", False, 5),
        ("shein", "shopping", False, 5),

        # Entertainment & Subscriptions
        ("movie", "movies", False, 5),
        ("cinema", "movies", False, 5),
        ("theater", "movies", False, 5),
        ("amc", "movies", False, 5),
        ("regal", "movies", False, 5),
        ("netflix", "subscriptions", False, 5),
        ("hulu", "subscriptions", False, 5),
        ("spotify", "subscriptions", False, 5),
        ("apple music", "subscriptions", False, 5),
        ("disney+", "subscriptions", False, 5),
        ("hbo", "subscriptions", False, 5),
        ("prime video", "subscriptions", False, 5),
        ("paramount+", "subscriptions", False, 5),
        ("game", "entertainment", False, 4),
        ("playstation", "entertainment", False, 5),
        ("xbox", "entertainment", False, 5),
        ("nintendo", "entertainment", False, 5),
        ("concert", "entertainment", False, 5),
        ("festival", "entertainment", False, 5),
        ("sports ticket", "entertainment", False, 5),

        # Health & Wellness
        ("gym", "health", False, 5),
        ("fitness", "health", False, 5),
        ("doctor", "health", False, 5),
        ("dentist", "health", False, 5),
        ("hospital", "health", False, 5),
        ("pharmacy", "health", False, 5),
        ("walgreens", "health", False, 5),
        ("cvs", "health", False, 5),
        ("rite aid", "health", False, 5),
        ("vision", "health", False, 5),
        ("glasses", "health", False, 5),
        ("contacts", "health", False, 5),
        ("insurance", "health", False, 5),
    ]


    # Create the mappings
    for keyword, category_key, is_regex, priority in default_mappings:
        # Check if we have a matching category for this keyword
        if category_key in categories:
            category_id = categories[category_key]

            # Create the mapping
            mapping = CategoryMapping(
                user_id=user_id,
                keyword=keyword,
                category_id=category_id,
                is_regex=is_regex,
                priority=priority,
                match_count=0,
                active=True
            )

            db.session.add(mapping)

    # Commit all mappings at once
    try:
        db.session.commit()
        app.logger.info(f"Created default category mappings for user {user_id}")
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error creating default category mappings: {str(e)}")

# Then modify the existing create_default_categories function to also create mappings:


def update_currency_rates() -> int:
    """
    Update currency exchange rates using a public API
    Returns the number of currencies updated or -1 on error
    """
    try:
        # Get the base currency
        base_currency = Currency.query.filter_by(is_base=True).first()
        if not base_currency:
            app.logger.error("No base currency found. Cannot update rates.")
            return -1

        base_code = base_currency.code

        # Use ExchangeRate-API (free tier - https://www.exchangerate-api.com/)
        # Or you can use another free API like https://frankfurter.app/
        response = requests.get(f'https://api.frankfurter.app/latest?from={base_code}')

        if response.status_code != 200:
            app.logger.error(f"API request failed with status code {response.status_code}")
            return -1

        data = response.json()
        rates = data.get('rates', {})

        # Get all currencies except base
        currencies = Currency.query.filter(Currency.code != base_code).all()
        updated_count = 0

        # Update rates
        for currency in currencies:
            if currency.code in rates:
                currency.rate_to_base = 1 / rates[currency.code]  # Convert to base currency rate
                currency.last_updated = datetime.utcnow()
                updated_count += 1
            else:
                app.logger.warning(f"No rate found for {currency.code}")

        # Commit changes
        db.session.commit()
        app.logger.info(f"Updated {updated_count} currency rates")
        return updated_count

    except Exception as e:
        app.logger.error(f"Error updating currency rates: {str(e)}")
        return -1

def convert_currency(amount: float, from_code: str, to_code: str) -> float:
    """Convert an amount from one currency to another"""
    if from_code == to_code:
        return amount

    from_currency = Currency.query.filter_by(code=from_code).first()
    to_currency = Currency.query.filter_by(code=to_code).first()

    if not from_currency or not to_currency:
        return amount  # Return original if either currency not found

    # Get base currency for reference
    base_currency = Currency.query.filter_by(is_base=True).first()
    if not base_currency:
        return amount  # Cannot convert without a base currency

    # First convert amount to base currency
    if from_code == base_currency.code:
        # Amount is already in base currency
        amount_in_base = amount
    else:
        # Convert from source currency to base currency
        # The rate_to_base represents how much of the base currency
        # equals 1 unit of this currency
        amount_in_base = amount * from_currency.rate_to_base

    # Then convert from base currency to target currency
    if to_code == base_currency.code:
        # Target is base currency, so we're done
        return amount_in_base
    else:
        # Convert from base currency to target currency
        # We divide by the target currency's rate_to_base to get
        # the equivalent amount in the target currency
        return amount_in_base / to_currency.rate_to_base

def create_scheduled_expenses():
    """Create expense instances for active recurring expenses"""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # Find active recurring expenses
    active_recurring = RecurringExpense.query.filter_by(active=True).all()

    for recurring in active_recurring:
        # Skip if end date is set and passed
        if recurring.end_date and recurring.end_date < today:
            continue

        # Determine if we need to create an expense based on frequency and last created date
        create_expense = False
        last_date = recurring.last_created or recurring.start_date

        if recurring.frequency == 'daily':
            # Create if last was created yesterday or earlier
            if (today - last_date).days >= 1:
                create_expense = True

        elif recurring.frequency == 'weekly':
            # Create if last was created 7 days ago or more
            if (today - last_date).days >= 7:
                create_expense = True

        elif recurring.frequency == 'monthly':
            # Create if we're in a new month from the last creation
            last_month = last_date.month
            last_year = last_date.year

            current_month = today.month
            current_year = today.year

            if current_year > last_year or (current_year == last_year and current_month > last_month):
                create_expense = True

        elif recurring.frequency == 'yearly':
            # Create if we're in a new year from the last creation
            if today.year > last_date.year:
                create_expense = True

        # Create the expense if needed
        if create_expense:
            expense = recurring.create_expense_instance(today)
            db.session.add(expense)

    # Commit all changes
    if active_recurring:
        db.session.commit()
def calculate_iou_data(expenses, users):
    """Calculate who owes whom money based on expenses"""
    # Initialize data structure
    iou_data = {
        'owes_me': {},  # People who owe current user
        'i_owe': {},    # People current user owes
        'net_balance': 0  # Overall balance (positive if owed money)
    }

    # Calculate balances
    for expense in expenses:
        splits = expense.calculate_splits()

        # If current user is the payer
        if expense.paid_by == current_user.id:
            # Track what others owe current user
            for split in splits['splits']:
                user_id = split['email']
                user_name = split['name']
                amount = split['amount']

                if user_id not in iou_data['owes_me']:
                    iou_data['owes_me'][user_id] = {'name': user_name, 'amount': 0}
                iou_data['owes_me'][user_id]['amount'] += amount

        # If current user is in the splits (but not the payer)
        elif current_user.id in [split['email'] for split in splits['splits']]:
            payer_id = expense.paid_by
            payer = User.query.filter_by(id=payer_id).first()

            # Find current user's split amount
            current_user_split = next((split['amount'] for split in splits['splits'] if split['email'] == current_user.id), 0)

            if payer_id not in iou_data['i_owe']:
                iou_data['i_owe'][payer_id] = {'name': payer.name, 'amount': 0}
            iou_data['i_owe'][payer_id]['amount'] += current_user_split

    # Calculate net balance
    total_owed = sum(data['amount'] for data in iou_data['owes_me'].values())
    total_owing = sum(data['amount'] for data in iou_data['i_owe'].values())
    iou_data['net_balance'] = total_owed - total_owing

    return iou_data

def calculate_balances(user_id):
    """Calculate balances between the current user and all other users"""
    balances = {}

    # Step 1: Calculate balances from expenses
    expenses = Expense.query.filter(
        or_(
            Expense.paid_by == user_id,
            Expense.split_with.like(f'%{user_id}%')
        )
    ).all()

    for expense in expenses:
        splits = expense.calculate_splits()

        # If current user paid for the expense
        if expense.paid_by == user_id:
            # Add what others owe to current user
            for split in splits['splits']:
                other_user_id = split['email']
                if other_user_id != user_id:
                    if other_user_id not in balances:
                        other_user = User.query.filter_by(id=other_user_id).first()
                        balances[other_user_id] = {
                            'user_id': other_user_id,
                            'name': other_user.name if other_user else 'Unknown',
                            'email': other_user_id,
                            'amount': 0
                        }
                    balances[other_user_id]['amount'] += split['amount']
        else:
            # If someone else paid and current user owes them
            payer_id = expense.paid_by

            # Find current user's portion
            current_user_portion = 0

            # Check if current user is in the splits
            for split in splits['splits']:
                if split['email'] == user_id:
                    current_user_portion = split['amount']
                    break

            if current_user_portion > 0:
                if payer_id not in balances:
                    payer = User.query.filter_by(id=payer_id).first()
                    balances[payer_id] = {
                        'user_id': payer_id,
                        'name': payer.name if payer else 'Unknown',
                        'email': payer_id,
                        'amount': 0
                    }
                balances[payer_id]['amount'] -= current_user_portion

    # Step 2: Adjust balances based on settlements
    settlements = Settlement.query.filter(
        or_(
            Settlement.payer_id == user_id,
            Settlement.receiver_id == user_id
        )
    ).all()

    for settlement in settlements:
        if settlement.payer_id == user_id:
            # Current user paid money to someone else
            other_user_id = settlement.receiver_id
            if other_user_id not in balances:
                other_user = User.query.filter_by(id=other_user_id).first()
                balances[other_user_id] = {
                    'user_id': other_user_id,
                    'name': other_user.name if other_user else 'Unknown',
                    'email': other_user_id,
                    'amount': 0
                }
            # FIX: When current user pays someone, it INCREASES how much they owe the current user
            # Change from -= to +=
            balances[other_user_id]['amount'] += settlement.amount

        elif settlement.receiver_id == user_id:
            # Current user received money from someone else
            other_user_id = settlement.payer_id
            if other_user_id not in balances:
                other_user = User.query.filter_by(id=other_user_id).first()
                balances[other_user_id] = {
                    'user_id': other_user_id,
                    'name': other_user.name if other_user else 'Unknown',
                    'email': other_user_id,
                    'amount': 0
                }
            # FIX: When current user receives money, it DECREASES how much they're owed
            # Change from += to -=
            balances[other_user_id]['amount'] -= settlement.amount

    # Return only non-zero balances
    return [balance for balance in balances.values() if abs(balance['amount']) > 0.01]

def get_base_currency():
    """Get the current user's default currency or fall back to base currency if not set"""
    if current_user.is_authenticated and current_user.default_currency_code and current_user.default_currency:
        # User has set a default currency, use that
        return {
            'code': current_user.default_currency.code,
            'symbol': current_user.default_currency.symbol,
            'name': current_user.default_currency.name
        }
    else:
        # Fall back to system base currency if user has no preference
        base_currency = Currency.query.filter_by(is_base=True).first()
        if not base_currency:
            # Default to USD if no base currency is set
            return {'code': 'USD', 'symbol': '$', 'name': 'US Dollar'}
        return {
            'code': base_currency.code,
            'symbol': base_currency.symbol,
            'name': base_currency.name
        }


@app.context_processor
def utility_processor():
    def get_user_color(user_id):
        """
        Generate a consistent color for a user based on their ID
        This ensures the same user always gets the same color
        """
        import hashlib

        # Use MD5 hash to generate a consistent color
        hash_object = hashlib.md5(user_id.encode())
        hash_hex = hash_object.hexdigest()

        # Use the first 6 characters of the hash to create a color
        # This ensures a consistent but pseudo-random color
        r = int(hash_hex[:2], 16)
        g = int(hash_hex[2:4], 16)
        b = int(hash_hex[4:6], 16)

        # Ensure the color is not too light
        brightness = (r * 299 + g * 587 + b * 114) / 1000
        if brightness > 180:
            # If too bright, darken the color
            r = min(r * 0.7, 255)
            g = min(g * 0.7, 255)
            b = min(b * 0.7, 255)

        return f'rgb({r},{g},{b})'

    def get_user_by_id(user_id):
        """
        Retrieve a user by their ID
        Returns None if user not found to prevent template errors
        """
        return User.query.filter_by(id=user_id).first()

    def get_category_icon_html(category):
        """
        Generate HTML for a category icon with proper styling
        """
        if not category:
            return '<i class="fas fa-tag"></i>'

        icon = category.icon or 'fa-tag'
        color = category.color or '#6c757d'

        return f'<i class="fas {icon}" style="color: {color};"></i>'

    def get_categories_as_tree():
        """
        Return categories in a hierarchical structure for dropdowns
        """
        # Get top-level categories
        top_categories = Category.query.filter_by(
            user_id=current_user.id,
            parent_id=None
        ).order_by(Category.name).all()

        result = []

        # Build tree structure
        for category in top_categories:
            cat_data = {
                'id': category.id,
                'name': category.name,
                'icon': category.icon,
                'color': category.color,
                'subcategories': []
            }

            # Add subcategories
            for subcat in category.subcategories:
                cat_data['subcategories'].append({
                    'id': subcat.id,
                    'name': subcat.name,
                    'icon': subcat.icon,
                    'color': subcat.color
                })

            result.append(cat_data)

        return result

    def get_budget_status_for_category(category_id):
        """Get budget status for a specific category"""
        if not current_user.is_authenticated:
            return None

        # Find active budget for this category
        budget = Budget.query.filter_by(
            user_id=current_user.id,
            category_id=category_id,
            active=True
        ).first()

        if not budget:
            return None

        return {
            'id': budget.id,
            'percentage': budget.get_progress_percentage(),
            'status': budget.get_status(),
            'amount': budget.amount,
            'spent': budget.calculate_spent_amount(),
            'remaining': budget.get_remaining_amount()
        }

    def get_account_by_id(account_id):
        """Retrieve an account by its ID"""
        return Account.query.get(account_id)

    # Return a single dictionary containing all functions
    return {
        'get_user_color': get_user_color,
        'get_user_by_id': get_user_by_id,
        'get_category_icon_html': get_category_icon_html,
        'get_categories_as_tree': get_categories_as_tree,
        'get_budget_status_for_category': get_budget_status_for_category,
        'get_account_by_id': get_account_by_id
    }


# Add to utility_processor to make budget info available in templates
@app.context_processor
def utility_processor():
    # Previous utility functions...

    def get_budget_status_for_category(category_id):
        """Get budget status for a specific category"""
        if not current_user.is_authenticated:
            return None

        # Find active budget for this category
        budget = Budget.query.filter_by(
            user_id=current_user.id,
            category_id=category_id,
            active=True
        ).first()

        if not budget:
            return None

        return {
            'id': budget.id,
            'percentage': budget.get_progress_percentage(),
            'status': budget.get_status(),
            'amount': budget.amount,
            'spent': budget.calculate_spent_amount(),
            'remaining': budget.get_remaining_amount()
        }
    def template_convert_currency(amount, from_code, to_code):
        """Make convert_currency available to templates"""
        return convert_currency(amount, from_code, to_code)
    return {
        # Previous functions...
        'get_budget_status_for_category': get_budget_status_for_category,
        'convert_currency': template_convert_currency
    }
@app.context_processor
def inject_app_version():
    """Make app version available to all templates"""
    return {
        'app_version': APP_VERSION
    }

def handle_comparison_request():
    """Handle time frame comparison requests within the stats route"""
    # Get parameters from request
    primary_start = request.args.get('primaryStart')
    primary_end = request.args.get('primaryEnd')
    comparison_start = request.args.get('comparisonStart')
    comparison_end = request.args.get('comparisonEnd')
    metric = request.args.get('metric', 'spending')

    # Convert string dates to datetime objects
    try:
        primary_start_date = datetime.strptime(primary_start, '%Y-%m-%d')
        primary_end_date = datetime.strptime(primary_end, '%Y-%m-%d')
        comparison_start_date = datetime.strptime(comparison_start, '%Y-%m-%d')
        comparison_end_date = datetime.strptime(comparison_end, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400

    # Initialize response data structure
    result = {
        'primary': {
            'totalSpending': 0,
            'transactionCount': 0,
            'topCategory': 'None',
            'dailyAmounts': []  # Make sure this is initialized
        },
        'comparison': {
            'totalSpending': 0,
            'transactionCount': 0,
            'topCategory': 'None',
            'dailyAmounts': []  # Make sure this is initialized
        },
        'dateLabels': []  # Initialize date labels
    }

    # Get expenses for both periods - reuse your existing query logic
    primary_query_filters = [
        or_(
            Expense.user_id == current_user.id,
            Expense.split_with.like(f'%{current_user.id}%')
        ),
        Expense.date >= primary_start_date,
        Expense.date <= primary_end_date
    ]
    primary_expenses_raw = Expense.query.filter(and_(*primary_query_filters)).order_by(Expense.date).all()

    comparison_query_filters = [
        or_(
            Expense.user_id == current_user.id,
            Expense.split_with.like(f'%{current_user.id}%')
        ),
        Expense.date >= comparison_start_date,
        Expense.date <= comparison_end_date
    ]
    comparison_expenses_raw = Expense.query.filter(and_(*comparison_query_filters)).order_by(Expense.date).all()

    # Process expenses to get user's portion
    primary_expenses = []
    comparison_expenses = []
    primary_total = 0
    comparison_total = 0

    # Process primary period expenses
    for expense in primary_expenses_raw:
        splits = expense.calculate_splits()
        user_portion = 0

        if expense.paid_by == current_user.id:
            user_portion = splits['payer']['amount']
        else:
            for split in splits['splits']:
                if split['email'] == current_user.id:
                    user_portion = split['amount']
                    break

        if user_portion > 0:
            expense_data = {
                'id': expense.id,
                'description': expense.description,
                'date': expense.date,
                'total_amount': expense.amount,
                'user_portion': user_portion,
                'paid_by': expense.paid_by,
                'category_name': get_category_name(expense)
            }
            primary_expenses.append(expense_data)
            primary_total += user_portion

    # Process comparison period expenses
    for expense in comparison_expenses_raw:
        splits = expense.calculate_splits()
        user_portion = 0

        if expense.paid_by == current_user.id:
            user_portion = splits['payer']['amount']
        else:
            for split in splits['splits']:
                if split['email'] == current_user.id:
                    user_portion = split['amount']
                    break

        if user_portion > 0:
            expense_data = {
                'id': expense.id,
                'description': expense.description,
                'date': expense.date,
                'total_amount': expense.amount,
                'user_portion': user_portion,
                'paid_by': expense.paid_by,
                'category_name': get_category_name(expense)
            }
            comparison_expenses.append(expense_data)
            comparison_total += user_portion

    # Update basic metrics
    result['primary']['totalSpending'] = primary_total
    result['primary']['transactionCount'] = len(primary_expenses)
    result['comparison']['totalSpending'] = comparison_total
    result['comparison']['transactionCount'] = len(comparison_expenses)

    # Process data based on the selected metric
    if metric == 'spending':
        # Calculate daily spending for each period
        primary_daily = process_daily_spending(primary_expenses, primary_start_date, primary_end_date)
        comparison_daily = process_daily_spending(comparison_expenses, comparison_start_date, comparison_end_date)

        # Normalize to 10 data points for consistent display
        result['primary']['dailyAmounts'] = normalize_time_series(primary_daily, 10)
        result['comparison']['dailyAmounts'] = normalize_time_series(comparison_daily, 10)
        result['dateLabels'] = [f'Day {i+1}' for i in range(10)]

        # Debugging - log the daily spending data
        app.logger.info(f"Primary daily amounts: {result['primary']['dailyAmounts']}")
        app.logger.info(f"Comparison daily amounts: {result['comparison']['dailyAmounts']}")

    elif metric == 'categories':
        # Get category spending for both periods
        primary_categories = {}
        comparison_categories = {}

        # Process primary period categories
        for expense in primary_expenses:
            category = expense['category_name'] or 'Uncategorized'
            if category not in primary_categories:
                primary_categories[category] = 0
            primary_categories[category] += expense['user_portion']

        # Process comparison period categories
        for expense in comparison_expenses:
            category = expense['category_name'] or 'Uncategorized'
            if category not in comparison_categories:
                comparison_categories[category] = 0
            comparison_categories[category] += expense['user_portion']

        # Get top categories across both periods
        all_categories = set(list(primary_categories.keys()) + list(comparison_categories.keys()))
        top_categories = sorted(
            all_categories,
            key=lambda c: (primary_categories.get(c, 0) + comparison_categories.get(c, 0)),
            reverse=True
        )[:5]

        result['categoryLabels'] = top_categories
        result['primary']['categoryAmounts'] = [primary_categories.get(cat, 0) for cat in top_categories]
        result['comparison']['categoryAmounts'] = [comparison_categories.get(cat, 0) for cat in top_categories]

        # Set top category
        result['primary']['topCategory'] = max(primary_categories.items(), key=lambda x: x[1])[0] if primary_categories else 'None'
        result['comparison']['topCategory'] = max(comparison_categories.items(), key=lambda x: x[1])[0] if comparison_categories else 'None'

    elif metric == 'tags':
        # Similar logic for tags - adapt based on your data model
        primary_tags = {}
        comparison_tags = {}

        # For primary period
        for expense in primary_expenses:
            # Get tags for this expense - adapt to your model
            expense_obj = Expense.query.get(expense['id'])
            if expense_obj and hasattr(expense_obj, 'tags'):
                for tag in expense_obj.tags:
                    if tag.name not in primary_tags:
                        primary_tags[tag.name] = 0
                    primary_tags[tag.name] += expense['user_portion']

        # For comparison period
        for expense in comparison_expenses:
            expense_obj = Expense.query.get(expense['id'])
            if expense_obj and hasattr(expense_obj, 'tags'):
                for tag in expense_obj.tags:
                    if tag.name not in comparison_tags:
                        comparison_tags[tag.name] = 0
                    comparison_tags[tag.name] += expense['user_portion']

        # Get top tags
        all_tags = set(list(primary_tags.keys()) + list(comparison_tags.keys()))
        top_tags = sorted(
            all_tags,
            key=lambda t: (primary_tags.get(t, 0) + comparison_tags.get(t, 0)),
            reverse=True
        )[:5]

        result['tagLabels'] = top_tags
        result['primary']['tagAmounts'] = [primary_tags.get(tag, 0) for tag in top_tags]
        result['comparison']['tagAmounts'] = [comparison_tags.get(tag, 0) for tag in top_tags]

    elif metric == 'payment':
        # Payment method comparison
        primary_payment = {}
        comparison_payment = {}

        # For primary period - only count what the user paid directly
        for expense in primary_expenses:
            if expense['paid_by'] == current_user.id:
                # Get the payment method (assuming it's stored as card_used)
                expense_obj = Expense.query.get(expense['id'])
                if expense_obj and hasattr(expense_obj, 'card_used'):
                    card = expense_obj.card_used
                    if card not in primary_payment:
                        primary_payment[card] = 0
                    primary_payment[card] += expense['user_portion']

        # For comparison period
        for expense in comparison_expenses:
            if expense['paid_by'] == current_user.id:
                expense_obj = Expense.query.get(expense['id'])
                if expense_obj and hasattr(expense_obj, 'card_used'):
                    card = expense_obj.card_used
                    if card not in comparison_payment:
                        comparison_payment[card] = 0
                    comparison_payment[card] += expense['user_portion']

        # Combine payment methods
        all_methods = set(list(primary_payment.keys()) + list(comparison_payment.keys()))

        result['paymentLabels'] = list(all_methods)
        result['primary']['paymentAmounts'] = [primary_payment.get(method, 0) for method in all_methods]
        result['comparison']['paymentAmounts'] = [comparison_payment.get(method, 0) for method in all_methods]

    return jsonify(result)


# Context processor for timezone-aware datetime formatting
@app.context_processor
def timezone_processor():
    def format_datetime(dt, format="medium"):
        """Format datetime in user's local timezone."""
        if not dt:
            return ""

        # Ensure dt is timezone-aware
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)

        # Convert to user's timezone
        if current_user.is_authenticated:
            user_tz = pytz.timezone(current_user.timezone or "UTC")
            local_dt = dt.astimezone(user_tz)
        else:
            local_dt = dt

        # Format based on preference
        if format == "short":
            return local_dt.strftime("%Y-%m-%d")
        if format == "medium":
            return local_dt.strftime("%Y-%m-%d %H:%M")
        if format == "long":
            return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")

        return local_dt

    return {"format_datetime": format_datetime}


#--------------------
# DATABASE INITIALIZATION
#--------------------

# Database initialization at application startup
with app.app_context():
    try:
        print("Creating database tables...")
        db.create_all()
        init_db()
        init_default_currencies()
        print("Tables created successfully")
    except Exception as e:
        print(f"ERROR CREATING TABLES: {str(e)}")

# Register OIDC routes
if oidc_enabled:
    register_oidc_routes(app, User, db)

if __name__ == '__main__':
    app.run(debug=True, port=5001)
