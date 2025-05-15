# gocardless_client.py
from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import timezone as tz
from typing import Any, Callable, ClassVar, cast

import requests
from flask import Flask

from models.account import Account
from models.expense import Expense


class GoCardlessClient:
    """A client for interacting with the GoCardless API.

    Handles setup token flow, account access, and transaction syncing
    """

    SUPPORTED_COUNTRIES: ClassVar[list[list[str]]] = [
        ["Austria", "AT"],
        ["Belgium", "BE"],
        ["Bulgaria", "BG"],
        ["Croatia", "HR"],
        ["Cyprus", "CY"],
        ["Czechia", "CZ"],
        ["Denmark", "DK"],
        ["Estonia", "ES"],
        ["Finland", "FI"],
        ["France", "FR"],
        ["Germany", "DE"],
        ["Greece", "GR"],
        ["Hungary", "HU"],
        ["Iceland", "IS"],
        ["Ireland", "IE"],
        ["Italy", "IT"],
        ["Latvia", "LV"],
        ["Liechtenstein", "LI"],
        ["Lithuania", "LT"],
        ["Luxembourg", "LU"],
        ["Malta", "MT"],
        ["Netherlands", "NL"],
        ["Norway", "NO"],
        ["Poland", "PL"],
        ["Portugal", "PT"],
        ["Romania", "RO"],
        ["Slovakia", "SK"],
        ["Slovenia", "SI"],
        ["Spain", "ES"],
        ["Sweden", "SE"],
        ["United Kingdom", "GB"],
    ]

    def init_app(self, app: Any) -> None:
        app.gocardless_client = self

        # Cast app to Flask type for type checking
        self.app: Flask = cast(Flask, app)
        self.secret_token_url: str = self.app.config.get(
            "GOCARDLESS_SECRET_TOKEN_URL",
            "https://bankaccountdata.gocardless.com/overview/",
        )

    def get_secret_token_url(self) -> str:
        """Return the URL where users can get their secret tokens."""
        return self.secret_token_url

    def get_countries(self) -> list[list[str]]:
        """Return the list of supported countries."""
        return self.SUPPORTED_COUNTRIES

    def get_tokens(self, secret_id: str, secret_key: str) -> dict | None:
        """Fetch access and refresh tokens using secret id and secret key."""
        try:
            resp: requests.Response = requests.post(
                "https://bankaccountdata.gocardless.com/api/v2/token/new/",
                json={"secret_id": secret_id, "secret_key": secret_key},
                headers={
                    "Content-Type": "application/json",
                    "accept": "application/json",
                },
            )
            response: dict[str, Any] = resp.json()
            if "access" in response:
                self.app.logger.info("GoCardless access token retrieved.")
                print(response["access"])
                return response
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception("Error fetching access token")
            return None

    def get_banks(self, access_token: str) -> list[dict[str, Any]] | None:
        """Retrieve list of all banks."""
        try:
            resp: requests.Response = requests.get(
                "https://bankaccountdata.gocardless.com/api/v2/institutions/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                },
            )
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception("Error fetching banks")
            return None

    def get_banks_by_country(
        self, access_token: str
    ) -> dict[str, list[dict[str, Any]]] | None:
        """Retrieve all banks, sorted by country."""
        banks: list[dict[str, Any]] | None = self.get_banks(access_token)
        if not banks:
            return None
        country_banks: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for bank in banks:
            for country in bank["countries"]:
                country_banks[country].append(bank)
        return country_banks

    def create_user_agreement(
        self,
        access_token: str,
        bank_id: str,
        max_historical_days: str,
        access_valid_for_days: str,
        access_scope: list[str],
    ) -> dict[str, Any] | None:
        """Create a user agreement with the supplied parameters."""
        try:
            resp: requests.Response = requests.post(
                "https://bankaccountdata.gocardless.com/api/v2/agreements/enduser/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={
                    "institution_id": bank_id,
                    "max_historical_days": max_historical_days,
                    "access_valid_for_days": access_valid_for_days,
                    "access_scope": access_scope,
                },
            )
            print(resp.text)
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception("Error creating user agreement.")
            return None

    def create_requisition(
        self, access_token: str, bank_id: str, redirect_url: str, agreement: str
    ) -> dict[str, Any] | None:
        """Create a requisition for creating links and retrieving accounts."""
        try:
            resp: requests.Response = requests.post(
                "https://bankaccountdata.gocardless.com/api/v2/requisitions/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={
                    "institution_id": bank_id,
                    "redirect": redirect_url,
                    "agreement": agreement,
                },
            )
            print(resp.text)
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception("Error creating requisition.")
            return None

    def get_accounts(
        self, access_token: str, requisition: str
    ) -> dict[str, Any] | None:
        """Get accounts for specific requisition."""
        try:
            resp: requests.Response = requests.get(
                f"https://bankaccountdata.gocardless.com/api/v2/requisitions/{requisition}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                },
            )
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception("Error fetching accounts.")
            return None

    def get_account_transactions(
        self,
        access_token: str,
        account_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
        max_days: int = 90,
    ) -> dict[str, Any] | None:
        """Get transaction for specified account id."""
        # Default values for dates
        if date_from is None:
            date_from = (datetime.now(tz.utc) - timedelta(days=3)).date()
        if date_to is None:
            date_to = datetime.now(tz.utc).date()
        if abs(date_to - date_from).days > max_days:
            self.app.logger.warning(
                "Error fetching transactions for account %s, date length "
                "exceeds limit of %d days.",
                max_days,
                account_id,
            )
            return None
        try:
            resp: requests.Response = requests.get(
                f"https://bankaccountdata.gocardless.com/api/v2/accounts/{account_id}/transactions/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                },
            )
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception(
                "Error fetching transactions for account %s.", account_id
            )
            return None

    def get_account_balances(
        self, access_token: str, account_id: str
    ) -> dict[str, Any] | None:
        """Get account balances."""
        try:
            resp: requests.Response = requests.get(
                f"https://bankaccountdata.gocardless.com/api/v2/accounts/{account_id}/balances/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                },
            )
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception(
                "Error fetching account balances for account id %s.",
                account_id,
            )
            return None

    def get_basic_account_information(
        self, access_token: str, account_id: str
    ) -> dict[str, Any] | None:
        """Get basic account information such as owner and IBAN."""
        try:
            resp: requests.Response = requests.get(
                f"https://bankaccountdata.gocardless.com/api/v2/accounts/{account_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                },
            )
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception(
                "Error fetching basic account information for account id %s.",
                account_id,
            )
            return None

    def get_account_details(
        self, access_token: str, account_id: str
    ) -> dict[str, Any] | None:
        """Get account details for the specified account id."""
        try:
            resp: requests.Response = requests.get(
                f"https://bankaccountdata.gocardless.com/api/v2/accounts/{account_id}/details/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                },
            )
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception(
                "Error fetching account details for account id %s.", account_id
            )
            return None

    def get_institution_info(
        self, access_token: str, institution_id: str
    ) -> dict[str, Any] | None:
        """Get instituion info."""
        try:
            resp: requests.Response = requests.get(
                f"https://bankaccountdata.gocardless.com/api/v2/institutions/{institution_id}/",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                },
            )
            return resp.json()
        except requests.exceptions.JSONDecodeError:
            self.app.logger.exception(
                "Error fetching institution info for institution id %s.",
                institution_id,
            )
            return None

    def create_transactions(
        self,
        transactions: list[dict[str, Any]],
        gocardless_account: Account,
        detect_transfer_func: Callable[..., Any] | None = None,
        auto_categorize_func: Callable[..., Any] | None = None,
        get_category_id_func: Callable[..., Any] | None = None,
    ) -> tuple[list[Expense], int]:
        """Create Expense objects from transaction data."""
        transaction_num: int = 0
        imported_transactions: list[Expense] = []
        for t in transactions:
            transaction: Expense | None
            is_transfer: bool
            transaction, is_transfer = self.create_transaction_instance(
                t,
                gocardless_account,
                detect_transfer_func,
                auto_categorize_func,
                get_category_id_func,
            )
            if transaction:
                imported_transactions.append(transaction)
                transaction_num += 1
        return (imported_transactions, transaction_num)

    def create_transaction_instance(
        self,
        trans_data: dict[str, Any],
        db_account: Account,
        detect_transfer_func: Callable[..., Any] | None = None,
        auto_categorize_func: Callable[..., Any] | None = None,
        get_category_id_func: Callable[..., Any] | None = None,
    ):
        # Skip transactions without required fields
        if not all(
            key in trans_data
            for key in ["transactionId", "bookingDate", "transactionAmount"]
        ):
            return None, False

        # Detect internal transfer if function provided
        is_transfer = False
        source_account_id: int | None = db_account.id if db_account else None
        destination_account_id = None

        if detect_transfer_func and source_account_id:
            try:
                is_transfer, source_account_id, destination_account_id = (
                    detect_transfer_func(
                        trans_data.get("description", ""),
                        trans_data.get(
                            "raw_amount", trans_data.get("amount", 0)
                        ),  # Use raw amount with sign
                        source_account_id,
                    )
                )

                if is_transfer:
                    transaction_type = "transfer"
                else:
                    amount: float = float(
                        trans_data.get("transactionAmount", {}).get("amount", 0)
                    )
                    if amount < 0:
                        transaction_type = "expense"
                        amount = abs(amount)  # Store as positive
                    elif amount > 0:
                        transaction_type = "income"
                    else:
                        return None, False  # Skip zero-amount transactions
            except Exception as e:
                self.app.logger.error(f"Error in transfer detection: {str(e)}")
                is_transfer = False
                transaction_type = trans_data.get("transaction_type", "expense")
        else:
            # No transfer detection, use the type from data
            transaction_type = trans_data.get("transaction_type", "expense")

        # Create the transaction instance
        transaction = Expense(
            description=trans_data.get("description", "Unknown Transaction"),
            amount=trans_data.get("transactionAmount", {}).get("amount", 0),
            date=datetime.strptime(
                trans_data.get("bookingDate", "1970-01-01"), "%Y-%m-%d"
            ).date(),
            card_used=db_account.name if db_account else "Unknown Account",
            transaction_type=transaction_type,
            split_method="equal",  # Default for imports
            paid_by=db_account.user_id,
            user_id=db_account.user_id,
            account_id=source_account_id,
            destination_account_id=destination_account_id,
            external_id=trans_data.get("external_id"),
            import_source="simplefin",
            split_with=None,  # Personal expense by default
        )

        # Apply auto-categorization for non-transfers
        if transaction_type != "transfer":
            category_id = None

            # Try auto-categorization first if function provided
            if auto_categorize_func:
                try:
                    category_id = auto_categorize_func(
                        trans_data.get("description", ""), db_account.user_id
                    )
                except Exception as e:
                    self.app.logger.error(
                        f"Error in auto-categorization: {str(e)}"
                    )

            # If auto-categorization didn't find a match but SimpleFin provided a category name,
            # try to find or create a matching category
            if (
                not category_id
                and trans_data.get("category_name")
                and get_category_id_func
            ):
                try:
                    category_id = get_category_id_func(
                        trans_data.get("category_name"),
                        trans_data.get("description"),
                        db_account.user_id,
                    )
                except Exception as e:
                    self.app.logger.error(f"Error in category lookup: {str(e)}")

            # Set the category if we found one
            if category_id:
                transaction.category_id = category_id

        return transaction, is_transfer
