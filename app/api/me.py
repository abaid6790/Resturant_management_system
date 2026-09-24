from flask import Blueprint, g

from app.core.http import ok

bp = Blueprint("me", __name__)


@bp.get("/me")
def me():
    u = g.user
    return ok({
        "id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role.name,
        "permissions": sorted(u.permission_codes),
        "branches": [{"id": b.id, "code": b.code, "name": b.name} for b in g.allowed_branches],
        "current_branch_id": g.branch.id if g.branch else None,
    })
