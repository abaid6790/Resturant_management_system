"""First-run setup and idempotent seeding of permissions and system roles."""
from __future__ import annotations

from flask import current_app

from app.core import security
from app.core.errors import ValidationError
from app.core.permissions import ALL_CODES, CATALOG, DEFAULT_ROLES
from app.extensions import db
from app.models.auth import Branch, Permission, Role, User
from app.services import audit
from app.services import auth as auth_svc
from app.services import branches as branch_svc
from app.services import catalog as catalog_svc
from app.services import settings as settings_svc


def sync_permissions() -> int:
    """Insert catalogue permissions that are missing and refresh labels. Returns number added."""
    existing = {p.code: p for p in db.session.scalars(db.select(Permission))}
    added = 0
    for module, items in CATALOG.items():
        for code, label, sensitive in items:
            p = existing.get(code)
            if p is None:
                db.session.add(Permission(code=code, module=module, label=label, sensitive=sensitive))
                added += 1
            else:
                p.module, p.label, p.sensitive = module, label, sensitive
    db.session.flush()
    return added


def seed_roles() -> None:
    """Create missing system roles and standard units. Idempotent; existing rows are untouched."""
    sync_permissions()
    catalog_svc.seed_units()
    have = {r.name for r in db.session.scalars(db.select(Role))}
    perms = {p.code: p for p in db.session.scalars(db.select(Permission))}
    for name, spec in DEFAULT_ROLES.items():
        if name in have:
            continue
        db.session.add(Role(
            name=name, description=spec["description"], is_system=True, is_super=spec["is_super"],
            permissions=[perms[c] for c in spec["permissions"] if c in perms],
        ))
    db.session.flush()


def needs_setup() -> bool:
    if current_app.extensions.get("setup_done"):
        return False
    done = db.session.scalar(db.select(db.exists().where(User.id.isnot(None))))
    if done:
        current_app.extensions["setup_done"] = True
    return not done


def run_setup(data: dict) -> User:
    """Create the first branch, the settings and the first Super Admin. One transaction."""
    if not needs_setup():
        raise ValidationError("Setup has already been completed.")
    errors = {}
    if not (data.get("restaurant_name") or "").strip():
        errors["restaurant_name"] = "Enter the restaurant name."
    username = (data.get("username") or "").strip().lower()
    if len(username) < 3:
        errors["username"] = "Use at least 3 characters."
    if not (data.get("full_name") or "").strip():
        errors["full_name"] = "Enter your name."
    if data.get("password") != data.get("password2"):
        errors["password2"] = "The passwords do not match."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    seed_roles()
    branch = Branch(code=(data.get("branch_code") or "MAIN").strip().upper() or "MAIN",
                    name=(data.get("branch_name") or "Main Branch").strip() or "Main Branch",
                    is_active=True)
    db.session.add(branch)
    auth_svc.check_password(data.get("password") or "", username)
    super_role = db.session.scalar(db.select(Role).where(Role.is_super.is_(True)))
    user = User(username=username, full_name=data["full_name"].strip(), role=super_role,
                all_branches=True, is_active=True,
                password_hash=security.hash_password(data["password"]))
    db.session.add(user)
    db.session.flush()
    settings_svc.update({
        "restaurant.name": data["restaurant_name"], "locale.timezone": data.get("timezone") or "UTC",
        "currency.code": data.get("currency_code") or "USD",
        "currency.symbol": data.get("currency_symbol") or "$",
    }, user, keys=["restaurant.name", "locale.timezone", "currency.code", "currency.symbol"])
    audit.log("system.setup", "system", user=user, record_type="user", record_id=user.id,
              branch_id=branch.id, after={"restaurant": data["restaurant_name"].strip(),
                                          "branch": branch.code, "admin": user.username})
    current_app.extensions["setup_done"] = True
    return user


__all__ = ["ALL_CODES", "branch_svc", "needs_setup", "run_setup", "seed_roles", "sync_permissions"]
