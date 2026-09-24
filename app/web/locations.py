from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import ValidationError
from app.extensions import db
from app.services import branches as branch_svc
from app.services import catalog as svc

bp = Blueprint("locations", __name__, url_prefix="/inventory/locations")


@bp.get("/")
@require("inventory.manage")
def index():
    return render_template("locations/list.html", locations=svc.visible_locations(g.user))


def _form(existing=None):
    form, errors = {"branch_id": "", "name": "", "location_type": "store"}, {}
    if existing:
        form = {"branch_id": str(existing.branch_id), "name": existing.name,
                "location_type": existing.location_type}
    if request.method == "POST":
        form = {"branch_id": request.form.get("branch_id", ""), "name": request.form.get("name", ""),
                "location_type": request.form.get("location_type", "store")}
        try:
            svc.save_location(g.user, form, existing)
            db.session.commit()
            flash("Location saved." if existing else "Location created.", "success")
            return redirect(url_for("locations.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    status = 422 if errors else 200
    return render_template("locations/form.html", location=existing, form=form, errors=errors,
                           branches=branch_svc.visible_branches(g.user, include_inactive=False)), status


@bp.route("/new", methods=["GET", "POST"])
@require("inventory.manage")
def new():
    return _form()


@bp.route("/<int:location_id>/edit", methods=["GET", "POST"])
@require("inventory.manage")
def edit(location_id):
    return _form(svc.get_location_or_404(g.user, location_id))


@bp.post("/<int:location_id>/status")
@require("inventory.manage")
def status(location_id):
    loc = svc.get_location_or_404(g.user, location_id)
    svc.set_location_active(g.user, loc, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("locations.index"))
