from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import ValidationError
from app.core.settings_registry import REGISTRY
from app.extensions import db
from app.services import settings as svc

bp = Blueprint("settings", __name__, url_prefix="/admin/settings")


def _groups():
    out: dict[str, list] = {}
    for d in REGISTRY:
        out.setdefault(d.group, []).append(d)
    return out


@bp.get("/")
@require("settings.view")
def index():
    return render_template("settings.html", groups=_groups(), values=svc.all_values(), errors={})


@bp.post("/")
@require("settings.manage")
def save():
    try:
        changed = svc.update(request.form.to_dict(), g.user)
        db.session.commit()
        flash(f"Settings saved ({len(changed)} changed)." if changed else "Nothing changed.", "success")
        return redirect(url_for("settings.index"))
    except ValidationError as err:
        db.session.rollback()
        flash(err.message, "error")
        return render_template("settings.html", groups=_groups(), values=request.form.to_dict(),
                               errors=err.details or {}), 422
