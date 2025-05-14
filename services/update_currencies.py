import logging
from datetime import datetime
from datetime import timezone as tz

import requests

from extensions import db
from models import Currency

logger = logging.getLogger(__name__)


def update_currency_rates():
    """Update currency exchange rates using a public API More robust rate updating mechanism."""
    try:
        # Find the base currency
        base_currency = Currency.query.filter_by(is_base=True).first()

        if not base_currency:
            logger.error("No base currency found. Cannot update rates.")
            return -1

        base_code = base_currency.code
        logger.info(f"Updating rates with base currency: {base_code}")

        # Use Frankfurter API
        api_url = f"https://api.frankfurter.app/latest?from={base_code}"

        try:
            response = requests.get(api_url, timeout=10)
        except requests.RequestException:
            logger.exception("API request failed")
            return -1

        if response.status_code != 200:
            logger.error(f"API returned status code {response.status_code}")
            return -1

        try:
            data = response.json()
        except ValueError:
            logger.exception("Failed to parse API response")
            return -1

        rates = data.get("rates", {})

        # Always set base currency rate to 1.0
        base_currency.rate_to_base = 1.0

        # Get all currencies except base
        currencies = Currency.query.filter(Currency.code != base_code).all()
        updated_count = 0

        # Update rates
        for currency in currencies:
            if currency.code in rates:
                try:
                    # Convert the rate to base currency rate
                    currency.rate_to_base = 1 / rates[currency.code]
                    currency.last_updated = datetime.now(tz.utc)
                    updated_count += 1

                    logger.info(
                        f"Updated {currency.code}: rate = {currency.rate_to_base}"
                    )
                except (TypeError, ZeroDivisionError):
                    logger.exception(
                        f"Error calculating rate for {currency.code}"
                    )

        # Commit changes
        db.session.commit()

        logger.info(f"Successfully updated {updated_count} currency rates")
        return updated_count

    except Exception:
        logger.exception("Unexpected error in currency rate update")
        return -1
