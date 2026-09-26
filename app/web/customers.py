from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import ValidationError
from app.core.http import paginate
from app.extensions import db
from app.services import customers as svc

bp = Blueprint("customers", __name__, url_prefix="/pos/customers")


@bp.get("/")
@require("customers.view")
def index():
    q = request.args.get("q", "")
    customers, meta = paginate(svc.search(q))
    return render_template("customers/list.html", customers=customers, meta=meta, q=q)


@bp.route("/new", methods=["GET", "POST"])
@require("customers.manage")
def new():
    errors = {}
    if request.method == "POST":
        try:
            c = svc.create(g.user, request.form.to_dict())
            db.session.commit()
            flash(f"Added {c.name}.", "success")
            return redirect(url_for("customers.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("customers/form.html", customer=None, errors=errors, form=request.form), \
        (422 if errors else 200)


@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@require("customers.manage")
def edit(customer_id):
    c = svc.get_or_404(customer_id)
    form = {"name": c.name, "phone": c.phone or "", "email": c.email or "", "notes": c.notes or ""}
    errors = {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            svc.update(g.user, c, form)
            db.session.commit()
            flash("Customer saved.", "success")
            return redirect(url_for("customers.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("customers/form.html", customer=c, errors=errors, form=form), \
        (422 if errors else 200)


@bp.post("/<int:customer_id>/status")
@require("customers.manage")
def status(customer_id):
    c = svc.get_or_404(customer_id)
    svc.set_active(g.user, c, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("customers.index"))
