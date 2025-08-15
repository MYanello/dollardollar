from datetime import UTC, datetime

import requests
from flask import current_app

from database import db
from models import Currency

"""
This script updates currency exchange rates in the application database.
"""


def update_currency_rates() -> int:
    """Update currency exchange rates using a public API.

    More robust rate updating mechanism
    """
    try:
        # Find the base currency
        base_currency: Currency | None = (
            db.session.query(Currency).filter_by(is_base=True).first()
        )

        if not base_currency:
            current_app.logger.error(
                "No base currency found. Cannot update rates."
            )
            return -1

        base_code = base_currency.code
        current_app.logger.info(
            "Updating rates with base currency: %s", base_code
        )

        # Use Frankfurter API
        api_url: str = f"https://api.frankfurter.app/latest?from={base_code}"

        try:
            response: requests.Response = requests.get(api_url, timeout=10)
        except requests.RequestException:
            current_app.logger.exception("API request failed")
            return -1

        if response.status_code != 200:
            current_app.logger.error(
                "API returned status code %d", response.status_code
            )
            return -1

        try:
            data: dict = response.json()
        except ValueError:
            current_app.logger.exception("Failed to parse API response")
            return -1

        rates = data.get("rates", {})

        # Always set base currency rate to 1.0
        base_currency.rate_to_base = 1.0

        # Get all currencies except base
        currencies: list[Currency] = (
            db.session.query(Currency).filter(Currency.code != base_code).all()
        )
        updated_count = 0

        # Update rates
        for currency in currencies:
            if currency.code in rates:
                try:
                    # Convert the rate to base currency rate
                    currency.rate_to_base = 1 / rates[currency.code]
                    currency.last_updated = datetime.now(UTC)
                    updated_count += 1

                    current_app.logger.info(
                        "Updated %s: rate = %f",
                        currency.code,
                        currency.rate_to_base,
                    )
                except (TypeError, ZeroDivisionError):
                    current_app.logger.exception(
                        "Error calculating rate for %s", currency.code
                    )

        # Commit changes
        db.session.commit()

    except Exception:
        current_app.logger.exception(
            "Unexpected error in currency rate update."
        )
        return -1
    else:
        current_app.logger.info(
            "Successfully updated %d currency rates", updated_count
        )
        return updated_count
