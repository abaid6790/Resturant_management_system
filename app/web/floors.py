from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import BusinessRuleError, ValidationError
from app.extensions import db
from app.services import branches as branch_svc
from app.services import floors as svc

bp = Blueprint("floors", __name__, url_prefix="/pos/floors")


@bp.get("/")
@require("tables.view")
def index():
    return render_template("floors/list.html", floors=svc.visible_floors(g.user))


@bp.route("/new", methods=["GET", "POST"])
@require("tables.manage")
def new():
    errors = {}
    if request.method == "POST":
        try:
            f = svc.save_floor(g.user, request.form.to_dict())
            db.session.commit()
            flash(f"Floor {f.name} created.", "success")
            return redirect(url_for("floors.show", floor_id=f.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("floors/form.html", floor=None, errors=errors, form=request.form,
                           branches=branch_svc.visible_branches(g.user, include_inactive=False)), \
        (422 if errors else 200)


@bp.get("/<int:floor_id>")
@require("tables.view")
def show(floor_id):
    f = svc.get_floor_or_404(g.user, floor_id)
    return render_template("floors/show.html", floor=f)


@bp.post("/<int:floor_id>/status")
@require("tables.manage")
def status(floor_id):
    f = svc.get_floor_or_404(g.user, floor_id)
    svc.set_floor_active(g.user, f, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("floors.index"))


@bp.route("/<int:floor_id>/tables/new", methods=["GET", "POST"])
@require("tables.manage")
def new_table(floor_id):
    f = svc.get_floor_or_404(g.user, floor_id)
    errors = {}
    if request.method == "POST":
        try:
            svc.save_table(g.user, f, request.form.to_dict())
            db.session.commit()
            flash("Table added.", "success")
            return redirect(url_for("floors.show", floor_id=f.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("floors/table_form.html", floor=f, table=None, errors=errors,
                           form=request.form), (422 if errors else 200)


@bp.route("/tables/<int:table_id>/edit", methods=["GET", "POST"])
@require("tables.manage")
def edit_table(table_id):
    t = svc.get_table_or_404(g.user, table_id)
    errors = {}
    form = {"name": t.name, "capacity": str(t.capacity)}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            svc.save_table(g.user, t.floor, form, t)
            db.session.commit()
            flash("Table saved.", "success")
            return redirect(url_for("floors.show", floor_id=t.floor_id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("floors/table_form.html", floor=t.floor, table=t, errors=errors,
                           form=form), (422 if errors else 200)


@bp.post("/tables/<int:table_id>/status")
@require("tables.manage")
def table_status(table_id):
    t = svc.get_table_or_404(g.user, table_id)
    try:
        svc.set_table_active(g.user, t, request.form.get("active") == "1")
        db.session.commit()
    except BusinessRuleError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("floors.show", floor_id=t.floor_id))
