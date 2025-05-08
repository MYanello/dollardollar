from functools import wraps
from typing import Any

from flask import current_app, flash, redirect, url_for
from flask_login import current_user, login_required, login_user

from database import db
from models import User
from services.const import DEV_USER_EMAIL, DEV_USER_PASSWORD
from session_timeout import DemoTimeout


def login_required_dev(f):
    @wraps(f)
    def decorated_function(*args, **kwargs) -> Any:
        if current_app.config["DEVELOPMENT_MODE"]:
            if not current_user.is_authenticated:
                # Get or create dev user
                dev_user: User | None = (
                    db.session.query(User).filter_by(id=DEV_USER_EMAIL).first()
                )
                if not dev_user:
                    dev_user = User(
                        id=DEV_USER_EMAIL, name="Developer", is_admin=True
                    )
                    dev_user.set_password(DEV_USER_PASSWORD)
                    db.session.add(dev_user)
                    db.session.commit()
                # Auto login dev user
                login_user(dev_user)
            return f(*args, **kwargs)
        # Normal authentication for non-dev mode - fixed implementation
        return login_required(f)(*args, **kwargs)

    return decorated_function


def restrict_demo_access(f):
    @wraps(f)
    def decorated_function(*args, **kwargs) -> Any:
        demo_timeout: DemoTimeout | None = current_app.extensions.get(
            "demo_timeout"
        )
        if (
            current_user.is_authenticated
            and demo_timeout
            and demo_timeout.is_demo_user(current_user.id)
        ):
            flash("Demo users cannot access this page.")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)

    return decorated_function
