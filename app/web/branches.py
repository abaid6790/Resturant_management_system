from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import ValidationError
from app.extensions import db
from app.services import branches as svc

bp = Blueprint("branches", __name__, url_prefix="/admin/branches")


def _read() -> dict:
    return {k: request.form.get(k, "") for k in ("code", "name", "address", "phone")}


@bp.get("/")
@require("branches.manage")
def index():
    return render_template("branches/list.html", branches=svc.visible_branches(g.user))


@bp.route("/new", methods=["GET", "POST"])
@require("branches.manage")
def new():
    form, errors = {}, {}
    if request.method == "POST":
        form = _read()
        try:
            svc.create(g.user, form)
            db.session.commit()
            flash("Branch created.", "success")
            return redirect(url_for("branches.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    status = 422 if errors else 200
    return render_template("branches/form.html", branch=None, form=form, errors=errors), status


@bp.route("/<int:branch_id>/edit", methods=["GET", "POST"])
@require("branches.manage")
def edit(branch_id):
    b = svc.get_or_404(g.user, branch_id)
    form, errors = {"code": b.code, "name": b.name, "address": b.address or "", "phone": b.phone or ""}, {}
    if request.method == "POST":
        form = _read()
        try:
            svc.update(g.user, b, form)
            db.session.commit()
            flash("Branch saved.", "success")
            return redirect(url_for("branches.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("branches/form.html", branch=b, form=form, errors=errors), (422 if errors else 200)


@bp.post("/<int:branch_id>/status")
@require("branches.manage")
def status(branch_id):
    b = svc.get_or_404(g.user, branch_id)
    active = request.form.get("active") == "1"
    svc.set_active(g.user, b, active)
    db.session.commit()
    flash(f"{b.name} is now {'active' if active else 'inactive'}.", "success")
    return redirect(url_for("branches.index"))
