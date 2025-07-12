from flask_sqlalchemy import SQLAlchemy
from flask import current_app as app
from models import Currency
from extensions import init_db


db = SQLAlchemy()

def init_default_currencies():
    """Initialize the default currencies in the database"""
    # Check if any currencies exist
    if len(db.session.query(Currency).all()) == 0:
        # Add USD as base currency
        usd = Currency(
            code="USD",
            name="US Dollar",
            symbol="$",
            rate_to_base=1.0,
            is_base=True,
        )

        # Add some common currencies
        eur = Currency(
            code="EUR",
            name="Euro",
            symbol="€",
            rate_to_base=1.1,  # Example rate
            is_base=False,
        )

        gbp = Currency(
            code="GBP",
            name="British Pound",
            symbol="£",
            rate_to_base=1.3,  # Example rate
            is_base=False,
        )

        jpy = Currency(
            code="JPY",
            name="Japanese Yen",
            symbol="¥",
            rate_to_base=0.0091,  # Example rate
            is_base=False,
        )

        db.session.add(usd)
        db.session.add(eur)
        db.session.add(gbp)
        db.session.add(jpy)

        try:
            db.session.commit()
            print("Default currencies initialized")
        except Exception as e:
            db.session.rollback()
            print(f"Error initializing currencies: {str(e)}")

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
