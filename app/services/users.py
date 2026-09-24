from __future__ import annotations

import re

from app.core import security
from app.core.authz import allowed_branch_ids, can_assign_role
from app.core.errors import (
    BusinessRuleError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.extensions import db
from app.models.auth import Branch, Role, User, user_branches
from app.services import audit
from app.services import auth as auth_svc
from app.services import settings as settings_svc

FIELDS = ["username", "full_name", "email", "role_id", "all_branches", "is_active",
          "must_change_password"]
_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{2,31}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _snap(u: User) -> dict:
    d = audit.snapshot(u, FIELDS)
    d["branches"] = sorted(b.code for b in u.branches)
    return d


def visible_stmt(actor):
    """Users the actor may see. Restricted admins only see non-super users inside their branches."""
    stmt = db.select(User)
    ids = allowed_branch_ids(actor)
    if ids is None:
        return stmt
    in_scope = db.select(user_branches.c.user_id).where(user_branches.c.branch_id.in_(ids))
    return stmt.where(User.all_branches.is_(False), User.role.has(Role.is_super.is_(False)),
                      User.id.in_(in_scope))


def get_or_404(actor, user_id: int) -> User:
    u = db.session.scalar(visible_stmt(actor).where(User.id == user_id))
    if u is None:
        raise NotFoundError("User not found.")  # same answer for hidden and missing users
    return u


def search(actor, q: str = "", status: str = "", role_id: int | None = None):
    stmt = visible_stmt(actor).order_by(User.full_name)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(db.or_(User.username.ilike(like), User.full_name.ilike(like),
                                 User.email.ilike(like)))
    if status in ("active", "inactive"):
        stmt = stmt.where(User.is_active.is_(status == "active"))
    if role_id:
        stmt = stmt.where(User.role_id == role_id)
    return stmt


def ensure_manageable(actor, target: User) -> None:
    """You may only change users whose access fits inside your own (no takeover of stronger accounts)."""
    if actor.is_super:
        return
    if target.role.is_super or not target.role.permission_codes <= actor.permission_codes:
        raise PermissionDeniedError("You cannot manage a user who has more access than you do.")


def _active_supers_excluding(user: User) -> int:
    return db.session.scalar(
        db.select(db.func.count(User.id)).join(Role)
        .where(Role.is_super.is_(True), User.is_active.is_(True), User.id != user.id)
    )


def _clean(actor, data: dict, existing: User | None) -> dict:
    errors, out = {}, {}
    username = (data.get("username") or "").strip().lower()
    if existing is None:
        if not _USERNAME.fullmatch(username):
            errors["username"] = "Use 3–32 letters, numbers, dots, dashes or underscores."
        elif auth_svc.find_user(username):
            errors["username"] = "That username is already taken."
        out["username"] = username
    out["full_name"] = (data.get("full_name") or "").strip()
    if not out["full_name"] or len(out["full_name"]) > 120:
        errors["full_name"] = "Enter the person's full name."
    email = (data.get("email") or "").strip()
    if email and not (_EMAIL.fullmatch(email) and len(email) <= 255):
        errors["email"] = "Enter a valid email address."
    out["email"] = email or None

    role = db.session.get(Role, int(data["role_id"])) if str(data.get("role_id") or "").isdigit() else None
    if role is None:
        errors["role_id"] = "Choose a role."
    elif existing is not None and existing.id == actor.id and role.id != existing.role_id:
        errors["role_id"] = "You cannot change your own role."
    elif not can_assign_role(actor, role) and not (existing and role.id == existing.role_id):
        raise PermissionDeniedError("You cannot assign a role with more access than your own.")
    out["role"] = role

    actor_ids = allowed_branch_ids(actor)
    all_branches = bool(data.get("all_branches"))
    if all_branches and actor_ids is not None:
        raise PermissionDeniedError("Only users with access to every branch can grant that.")
    wanted = {int(x) for x in data.get("branch_ids") or [] if str(x).isdigit()}
    if actor_ids is not None and not wanted <= actor_ids:
        raise PermissionDeniedError("You can only assign branches that you have access to.")
    branches = list(db.session.scalars(db.select(Branch).where(Branch.id.in_(wanted)))) if wanted else []
    if existing is not None and actor_ids is not None:  # keep branches outside the actor's scope
        branches += [x for x in existing.branches if x.id not in actor_ids]
    if role is not None and not role.is_super and not all_branches and not branches:
        errors["branch_ids"] = "Choose at least one branch, or allow all branches."
    if existing is not None and existing.id == actor.id:
        if all_branches != existing.all_branches or wanted != {b.id for b in existing.branches}:
            errors["branch_ids"] = "You cannot change your own branch access."
    out["all_branches"], out["branches"] = all_branches, branches
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    return out


def create(actor, data: dict) -> User:
    clean = _clean(actor, data, None)
    auth_svc.check_password(data.get("password") or "", clean["username"])
    u = User(username=clean["username"], full_name=clean["full_name"], email=clean["email"],
             role=clean["role"], all_branches=clean["all_branches"], is_active=True,
             must_change_password=bool(data.get("must_change_password", True)),
             password_hash=security.hash_password(data["password"]))
    u.branches = clean["branches"]
    db.session.add(u)
    db.session.flush()
    audit.log("user.create", "users", record_type="user", record_id=u.id, after=_snap(u))
    return u


def update(actor, u: User, data: dict) -> User:
    ensure_manageable(actor, u)
    clean = _clean(actor, data, u)
    before = _snap(u)
    if u.role.is_super and not clean["role"].is_super and _active_supers_excluding(u) == 0:
        raise BusinessRuleError("There must always be at least one active Super Admin.")
    u.full_name, u.email, u.role = clean["full_name"], clean["email"], clean["role"]
    u.all_branches, u.branches = clean["all_branches"], clean["branches"]
    db.session.flush()
    after = _snap(u)
    audit.log_change("user.update", "users", before, after, record_type="user", record_id=u.id)
    if before["role_id"] != after["role_id"] or before["branches"] != after["branches"]:
        auth_svc.revoke_all_sessions(u.id)  # force re-evaluation of access
    return u


def set_active(actor, u: User, active: bool) -> None:
    ensure_manageable(actor, u)
    if u.is_active == active:
        return
    if not active:
        if u.id == actor.id:
            raise BusinessRuleError("You cannot deactivate your own account.")
        if u.role.is_super and _active_supers_excluding(u) == 0:
            raise BusinessRuleError("There must always be at least one active Super Admin.")
    before = _snap(u)
    u.is_active = active
    if not active:
        auth_svc.revoke_all_sessions(u.id)
    audit.log_change("user.activate" if active else "user.deactivate", "users", before, _snap(u),
                     record_type="user", record_id=u.id)


def reset_password(actor, u: User, new_password: str) -> None:
    ensure_manageable(actor, u)
    if u.id == actor.id:
        raise BusinessRuleError("Use “Change password” in your account to change your own password.")
    auth_svc.set_password(u, new_password, actor, must_change=True, action="password.reset")


def unlock(actor, u: User) -> None:
    ensure_manageable(actor, u)
    u.failed_logins, u.locked_until = 0, None
    audit.log("user.unlock", "users", record_type="user", record_id=u.id)


def min_password_length() -> int:
    return settings_svc.get("security.password_min_length")
