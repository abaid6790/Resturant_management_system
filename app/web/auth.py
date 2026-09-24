from __future__ import annotations

from urllib.parse import urlparse
from zoneinfo import available_timezones

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

from app.core import security
from app.core.authz import public
from app.core.errors import (
    AuthenticationError,
    TooManyRequestsError,
    ValidationError,
)
from app.extensions import db
from app.services import auth as auth_svc
from app.services import bootstrap
from app.services import settings as settings_svc

bp = Blueprint("auth", __name__)


def safe_next(target: str | None) -> str:
    if target:
        p = urlparse(target)
        if not p.scheme and not p.netloc and target.startswith("/") and not target.startswith("//"):
            return target
    return url_for("home.index")


@bp.route("/login", methods=["GET", "POST"])
@public
def login():
    if g.user:
        return redirect(url_for("home.index"))
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "")
        try:
            if not current_app.extensions["login_limiter"].allow(request.remote_addr or "?"):
                raise TooManyRequestsError("Too many sign-in attempts. Wait a few minutes and try again.")
            user = auth_svc.authenticate(username, request.form.get("password", ""))
        except (AuthenticationError, TooManyRequestsError) as err:
            return render_template("login.html", error=err.message, username=username), err.status_code
        session.clear()
        token = auth_svc.start_session(user)
        db.session.commit()
        session["sid"], session["csrf"] = token, security.new_token()
        return redirect(safe_next(request.args.get("next")))
    return render_template("login.html", error=None, username=username)


@bp.post("/logout")
def logout():
    auth_svc.end_session(g.session, g.user)
    db.session.commit()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/setup", methods=["GET", "POST"])
@public
def setup():
    if not bootstrap.needs_setup():
        return redirect(url_for("auth.login"))
    form, errors = {"timezone": "UTC", "currency_code": "USD", "currency_symbol": "$",
                    "branch_code": "MAIN", "branch_name": "Main Branch"}, {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            bootstrap.run_setup(form)
            db.session.commit()
            flash("Setup complete. Sign in with the administrator account you just created.", "success")
            return redirect(url_for("auth.login"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("setup.html", form=form, errors=errors,
                           timezones=sorted(available_timezones())), (422 if errors else 200)


@bp.route("/account/password", methods=["GET", "POST"])
def account_password():
    errors = {}
    if request.method == "POST":
        try:
            f = request.form
            if not security.verify_password(g.user.password_hash, f.get("current", "")):
                raise ValidationError("Check the highlighted fields.",
                                      details={"current": "That is not your current password."})
            if f.get("new") != f.get("confirm"):
                raise ValidationError("Check the highlighted fields.",
                                      details={"confirm": "The passwords do not match."})
            auth_svc.set_password(g.user, f.get("new", ""), g.user, must_change=False,
                                  keep_session_id=g.session.id)
            db.session.commit()
            flash("Password changed. Other devices have been signed out.", "success")
            return redirect(url_for("home.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
    return render_template("account_password.html", errors=errors,
                           min_length=settings_svc.get("security.password_min_length"),
                           forced=g.user.must_change_password), (422 if errors else 200)


@bp.post("/branch/switch")
def switch_branch():
    choice = request.form.get("branch_id", "")
    ids = {str(b.id) for b in g.allowed_branches}
    if choice == "all" and len(ids) > 1:
        session["branch_id"] = "all"
    elif choice in ids:
        session["branch_id"] = choice
    else:
        flash("You do not have access to that branch.", "error")
    return redirect(safe_next(request.form.get("next")))
