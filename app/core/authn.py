"""Request pipeline: session loading, CSRF, default-deny, forced password change, branch context."""
from __future__ import annotations

import hmac
from datetime import UTC, datetime

from flask import Flask, g, redirect, request, session, url_for

from app.core import security
from app.core.authz import can, public
from app.core.errors import AuthenticationError, BadRequestError, ConflictError, wants_json
from app.core.ratelimit import RateLimiter
from app.services import auth as auth_svc
from app.services import bootstrap
from app.services import branches as branch_svc
from app.services import settings as settings_svc

SAFE = {"GET", "HEAD", "OPTIONS"}
ALLOWED_WHEN_PW_CHANGE_DUE = {"auth.account_password", "auth.logout", "static"}


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = security.new_token()
    return session["csrf"]


def _resolve_branch() -> None:
    g.allowed_branches = branch_svc.visible_branches(g.user, include_inactive=False)
    sel = session.get("branch_id")
    g.branch = None
    if sel == "all" and len(g.allowed_branches) > 1:
        return
    chosen = next((b for b in g.allowed_branches if str(b.id) == str(sel)), None)
    g.branch = chosen or (g.allowed_branches[0] if g.allowed_branches else None)


NAV = [
    ("Overview", [("Home", "home.index", "home.", None)]),
    ("Menu and inventory", [
        ("Items", "items.index", "items.", "products.view"),
        ("Recipes", "recipes.index", "recipes.", "recipes.view"),
        ("Categories", "categories.index", "categories.", "inventory.manage"),
        ("Locations", "locations.index", "locations.", "inventory.manage"),
        ("Stock", "stock.overview", "stock.", "inventory.view"),
        ("Stock ledger", "stock.ledger", "stock.ledger", "inventory.view"),
        ("Batches & expiry", "stock.batches", "stock.batches", "inventory.view"),
        ("Adjust stock", "stock.adjust", "stock.adjust", "inventory.adjust"),
        ("Transfer stock", "stock.transfer", "stock.transfer", "inventory.transfer"),
        ("Record wastage", "stock.wastage", "stock.wastage", "inventory.wastage"),
    ]),
    ("Purchasing", [
        ("Suppliers", "suppliers.index", "suppliers.", "suppliers.view"),
        ("Purchase orders", "purchases.index", "purchases.", "purchases.view"),
        ("New purchase order", "purchases.new", "purchases.new", "purchases.create"),
        ("Record a return", "purchases.new_return", "purchases.new_return", "purchases.return"),
    ]),
    ("Administration", [
        ("Users", "users.index", "users.", "users.view"),
        ("Roles", "roles.index", "roles.", "roles.manage"),
        ("Branches", "branches.index", "branches.", "branches.manage"),
        ("Settings", "settings.index", "settings.", "settings.view"),
        ("Audit log", "audit.index", "audit.", "audit.view"),
    ]),
]


def init_auth(app: Flask) -> None:
    app.extensions["login_limiter"] = RateLimiter(app.config["LOGIN_RATE_LIMIT"],
                                                  app.config["LOGIN_RATE_WINDOW"])

    @app.before_request
    def _gate():
        g.user = g.session = g.branch = None
        g.allowed_branches = []
        ep = request.endpoint
        if ep == "static":
            return
        if bootstrap.needs_setup():
            if ep in ("auth.setup", "health.health") or ep is None:
                return
            if wants_json():
                raise ConflictError("Setup has not been completed.", code="setup_required")
            return redirect(url_for("auth.setup"))

        token = session.get("sid")
        if token:
            loaded = auth_svc.load_session(token)
            if loaded:
                g.session, g.user = loaded
            else:
                session.pop("sid", None)

        if request.method not in SAFE:
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
            expected = session.get("csrf") or ""
            # Both must be present: two empty strings must never count as a match.
            if not sent or not expected or not hmac.compare_digest(sent, expected):
                raise BadRequestError("Your form expired. Reload the page and try again.",
                                      code="csrf_failed")

        view = app.view_functions.get(ep) if ep else None
        if view is None or getattr(view, "_public", False):  # 404s and explicitly public views
            return
        if g.user is None:  # default deny
            raise AuthenticationError()
        if g.user.must_change_password and ep not in ALLOWED_WHEN_PW_CHANGE_DUE:
            return redirect(url_for("auth.account_password"))
        _resolve_branch()

    @app.context_processor
    def _globals():
        user = getattr(g, "user", None)
        try:
            name = settings_svc.get("restaurant.name")
        except Exception:  # noqa: BLE001 - error pages must render even if the DB is down
            name = "Restaurant ERP"
        nav = []
        if user:
            for group, items in NAV:
                vis = [(label, ep, prefix) for label, ep, prefix, perm in items
                       if perm is None or can(perm)]
                if vis:
                    nav.append((group, vis))
        return {"current_user": user, "current_branch": getattr(g, "branch", None),
                "allowed_branches": getattr(g, "allowed_branches", []), "can": can,
                "csrf_token": csrf_token, "restaurant_name": name, "nav": nav,
                "now_utc": datetime.now(UTC)}

    @app.template_filter("dt")
    def _dt(value, fmt="%d %b %Y, %H:%M"):
        if value is None:
            return ""
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(settings_svc.get("locale.timezone"))
        except Exception:  # noqa: BLE001
            tz = ZoneInfo("UTC")
        return value.astimezone(tz).strftime(fmt)


__all__ = ["csrf_token", "init_auth", "public"]
