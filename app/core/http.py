"""Response envelope and server-side pagination helpers."""
from __future__ import annotations

from flask import current_app, jsonify, request

from app.core.errors import ValidationError
from app.extensions import db


def ok(data=None, *, meta: dict | None = None, status: int = 200):
    body: dict = {"data": data}
    if meta:
        body["meta"] = meta
    return jsonify(body), status


def get_page_args() -> tuple[int, int]:
    cfg = current_app.config
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", cfg["DEFAULT_PAGE_SIZE"]))
    except ValueError as exc:
        raise ValidationError("page and per_page must be integers.") from exc
    if page < 1:
        raise ValidationError("page must be >= 1.")
    if not 1 <= per_page <= cfg["MAX_PAGE_SIZE"]:
        raise ValidationError(f"per_page must be between 1 and {cfg['MAX_PAGE_SIZE']}.")
    return page, per_page


def paginate(stmt):
    """Run a SQLAlchemy select() with server-side pagination. Returns (items, meta)."""
    page, per_page = get_page_args()
    p = db.paginate(stmt, page=page, per_page=per_page, error_out=False, count=True)
    meta = {"page": p.page, "per_page": p.per_page, "total": p.total, "pages": p.pages}
    return p.items, meta
