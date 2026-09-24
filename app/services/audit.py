"""
Audit trail. Entries are added to the CURRENT database session, so an audit row commits or
rolls back together with the business change it describes. Secrets are never recorded.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import g, has_request_context, request

from app.extensions import db
from app.models.audit import AuditLog

_NEVER = {"password", "password_hash", "token", "token_hash"}


def _jsonable(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (set, frozenset)):
        return sorted(_jsonable(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items() if k not in _NEVER}
    return v


def snapshot(obj, fields: list[str]) -> dict:
    return {f: _jsonable(getattr(obj, f)) for f in fields if f not in _NEVER}


def log(action: str, module: str, *, record_type: str | None = None, record_id=None,
        before: dict | None = None, after: dict | None = None, branch_id: int | None = None,
        reference: str | None = None, user=None, username: str | None = None) -> AuditLog:
    user = user or (getattr(g, "user", None) if has_request_context() else None)
    entry = AuditLog(
        user_id=user.id if user else None,
        username=(user.username if user else username),
        action=action, module=module, record_type=record_type,
        record_id=None if record_id is None else str(record_id),
        branch_id=branch_id, reference=reference,
        before=_jsonable(before) if before is not None else None,
        after=_jsonable(after) if after is not None else None,
        ip=request.remote_addr if has_request_context() else None,
        request_id=getattr(g, "request_id", None) if has_request_context() else None,
    )
    db.session.add(entry)
    return entry


def log_change(action: str, module: str, before: dict, after: dict, **kw) -> AuditLog | None:
    """Record only the fields that changed. Returns None (and logs nothing) if nothing changed."""
    changed = [k for k in after if before.get(k) != after.get(k)]
    if not changed:
        return None
    return log(action, module, before={k: before.get(k) for k in changed},
               after={k: after[k] for k in changed}, **kw)
