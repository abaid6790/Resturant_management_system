"""Authorization: permission checks, default-deny markers and branch scoping (backend-enforced)."""
from __future__ import annotations

from functools import wraps

from flask import g

from app.core.errors import AuthenticationError, PermissionDeniedError


def public(fn):
    """Mark a view as reachable without signing in. Everything else is denied by default."""
    fn._public = True
    return fn


def can(code: str) -> bool:
    user = getattr(g, "user", None)
    return bool(user and user.has_permission(code))


def require(*codes: str, any_of: bool = False):
    """Backend permission check. All codes required unless any_of=True."""

    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = getattr(g, "user", None)
            if user is None:
                raise AuthenticationError()
            ok = (any if any_of else all)(user.has_permission(c) for c in codes)
            if not ok:
                raise PermissionDeniedError("You do not have permission to do that.")
            return fn(*a, **kw)

        return wrapper

    return deco


# ---- branch scoping ---------------------------------------------------------------------------

def allowed_branch_ids(user=None) -> set[int] | None:
    """None means unrestricted (all branches). Otherwise the set of ACTIVE branch ids."""
    user = user or g.user
    if user.all_branches or user.is_super:
        return None
    return {b.id for b in user.branches if b.is_active}


def ensure_branch_access(branch_id: int, user=None) -> None:
    ids = allowed_branch_ids(user)
    if ids is not None and branch_id not in ids:
        raise PermissionDeniedError("You do not have access to that branch.")


def branch_scope(stmt, column, user=None, *, use_current: bool = False):
    """
    Restrict a SELECT to the branches the user may see. EVERY branch-owned query in later phases
    must go through this. With use_current=True it also narrows to the branch picked in the switcher.
    """
    ids = allowed_branch_ids(user)
    if use_current and getattr(g, "branch", None) is not None:
        return stmt.where(column == g.branch.id)
    if ids is not None:
        return stmt.where(column.in_(ids))
    return stmt


# ---- privilege-escalation guards ----------------------------------------------------------------

def can_assign_role(actor, role) -> bool:
    """A user may only hand out roles that grant nothing beyond their own permissions."""
    if actor.is_super:
        return True
    return not role.is_super and role.permission_codes <= actor.permission_codes
