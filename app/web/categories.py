from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import ValidationError
from app.extensions import db
from app.services import catalog as svc

bp = Blueprint("categories", __name__, url_prefix="/inventory/categories")


@bp.get("/")
@require("inventory.manage")
def index():
    return render_template("categories/list.html", categories=svc.list_categories())


def _form(existing=None):
    form, errors = {"name": "", "description": ""}, {}
    if existing:
        form = {"name": existing.name, "description": existing.description or ""}
    if request.method == "POST":
        form = {"name": request.form.get("name", ""), "description": request.form.get("description", "")}
        try:
            svc.save_category(g.user, form, existing)
            db.session.commit()
            flash("Category saved." if existing else "Category created.", "success")
            return redirect(url_for("categories.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    status = 422 if errors else 200
    return render_template("categories/form.html", category=existing, form=form, errors=errors), status


@bp.route("/new", methods=["GET", "POST"])
@require("inventory.manage")
def new():
    return _form()


@bp.route("/<int:cid>/edit", methods=["GET", "POST"])
@require("inventory.manage")
def edit(cid):
    return _form(svc.get_category_or_404(cid))


@bp.post("/<int:cid>/status")
@require("inventory.manage")
def status(cid):
    c = svc.get_category_or_404(cid)
    svc.set_category_active(g.user, c, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("categories.index"))
