from flask import Blueprint, g, render_template

from app.core.authz import can
from app.extensions import db
from app.models.audit import AuditLog
from app.models.auth import User

bp = Blueprint("home", __name__)


@bp.get("/")
def index():
    stats = {}
    if can("users.view"):
        stats["users"] = db.session.scalar(db.select(db.func.count(User.id)).where(User.is_active.is_(True)))
    recent = []
    if can("audit.view"):
        recent = list(db.session.scalars(db.select(AuditLog).order_by(AuditLog.id.desc()).limit(6)))
    return render_template("home.html", stats=stats, recent=recent, perm_count=len(g.user.permission_codes))
