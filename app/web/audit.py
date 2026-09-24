from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, g, render_template, request

from app.core.authz import allowed_branch_ids, require
from app.core.http import paginate
from app.extensions import db
from app.models.audit import AuditLog
from app.services import settings as settings_svc

bp = Blueprint("audit", __name__, url_prefix="/admin/audit")


def _day_bounds(value: str, end: bool):
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    tz = ZoneInfo(settings_svc.get("locale.timezone"))
    start = datetime.combine(d, time.min, tzinfo=tz)
    return start + timedelta(days=1) if end else start


@bp.get("/")
@require("audit.view")
def index():
    a = request.args
    stmt = db.select(AuditLog).order_by(AuditLog.id.desc())
    ids = allowed_branch_ids(g.user)
    if ids is not None:
        stmt = stmt.where(AuditLog.branch_id.in_(ids))
    if a.get("from") and (lo := _day_bounds(a["from"], False)):
        stmt = stmt.where(AuditLog.created_at >= lo)
    if a.get("to") and (hi := _day_bounds(a["to"], True)):
        stmt = stmt.where(AuditLog.created_at < hi)
    if a.get("user"):
        stmt = stmt.where(AuditLog.username.ilike(f"%{a['user'].strip()}%"))
    if a.get("module"):
        stmt = stmt.where(AuditLog.module == a["module"])
    if a.get("action"):
        stmt = stmt.where(AuditLog.action.ilike(f"{a['action'].strip()}%"))
    if a.get("q"):
        q = a["q"].strip()
        stmt = stmt.where(db.or_(AuditLog.record_id == q, AuditLog.reference.ilike(f"%{q}%")))
    entries, meta = paginate(stmt)
    modules = db.session.scalars(db.select(AuditLog.module).distinct().order_by(AuditLog.module)).all()
    return render_template("audit/list.html", entries=entries, meta=meta, modules=modules, f=a)
