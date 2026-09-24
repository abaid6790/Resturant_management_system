import logging

from flask import Blueprint
from sqlalchemy import text

from app.core.authz import public
from app.core.http import ok
from app.extensions import db

log = logging.getLogger(__name__)
bp = Blueprint("health", __name__)


@bp.get("/health")
@public
def health():
    from app import __version__

    try:
        db.session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001 - health must never raise
        log.exception("Health check: database unreachable")
        database = "unavailable"
    status = "ok" if database == "ok" else "degraded"
    return ok({"status": status, "database": database, "version": __version__},
              status=200 if status == "ok" else 503)
