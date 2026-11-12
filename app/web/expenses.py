from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import ValidationError
from app.core.http import paginate
from app.extensions import db
from app.services import branches as branch_svc
from app.services import expenses as svc

bp = Blueprint("expenses", __name__, url_prefix="/finance/expenses")


@bp.get("/")
@require("expenses.view")
def index():
    a = request.args
    category_id = a.get("category_id", type=int)
    expenses, meta = paginate(svc.visible_stmt(g.user, category_id=category_id,
                                               date_from=a.get("from") or None,
                                               date_to=a.get("to") or None))
    return render_template("expenses/list.html", expenses=expenses, meta=meta, f=a,
                           categories=svc.list_categories())


@bp.route("/new", methods=["GET", "POST"])
@require("expenses.manage")
def new():
    errors = {}
    if request.method == "POST":
        try:
            e = svc.create(g.user, request.form.to_dict())
            db.session.commit()
            flash(f"Recorded {e.amount} expense.", "success")
            return redirect(url_for("expenses.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("expenses/form.html", errors=errors, form=request.form,
                           categories=svc.list_categories(include_inactive=False),
                           branches=branch_svc.visible_branches(g.user, include_inactive=False)), \
        (422 if errors else 200)


@bp.get("/categories")
@require("expenses.manage")
def categories():
    return render_template("expenses/categories.html", categories=svc.list_categories())


@bp.route("/categories/new", methods=["GET", "POST"])
@require("expenses.manage")
def new_category():
    errors = {}
    if request.method == "POST":
        try:
            svc.save_category(g.user, request.form.to_dict())
            db.session.commit()
            flash("Category created.", "success")
            return redirect(url_for("expenses.categories"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("expenses/category_form.html", category=None, errors=errors,
                           form=request.form), (422 if errors else 200)


@bp.post("/categories/<int:category_id>/status")
@require("expenses.manage")
def category_status(category_id):
    c = svc.get_category_or_404(category_id)
    svc.set_category_active(g.user, c, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("expenses.categories"))
