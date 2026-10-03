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


@bp.get("/<int:customer_id>")
@require("customers.view")
def show(customer_id):
    from app.core.http import paginate
    from app.services import customer_ledger, loyalty

    c = svc.get_or_404(customer_id)
    entries, meta = paginate(customer_ledger.ledger_stmt(customer_id, g.user))
    tx, ltx_meta = paginate(loyalty.transactions_stmt(customer_id)) if loyalty.enabled() else ([], None)
    return render_template("customers/show.html", customer=c, balance=customer_ledger.balance_of(customer_id),
                           entries=entries, meta=meta, loyalty_enabled=loyalty.enabled(),
                           loyalty_tx=tx, can_pay=g.user.has_permission("customers.credit"),
                           can_manage_loyalty=g.user.has_permission("loyalty.manage"))


@bp.post("/<int:customer_id>/pay")
@require("customers.credit")
def pay(customer_id):
    from app.core.errors import BusinessRuleError, ValidationError
    from app.services import customer_ledger

    c = svc.get_or_404(customer_id)
    data = request.form.to_dict()
    # The form has no branch field (most users only ever operate in one); default to whichever
    # branch is currently active (set by the switcher in the top bar), the same "which branch is
    # this for" context every other screen uses, rather than forcing an explicit picker here.
    if not data.get("branch_id") and g.branch:
        data["branch_id"] = str(g.branch.id)
    try:
        customer_ledger.record_payment(g.user, c, data)
        db.session.commit()
        flash("Payment recorded.", "success")
    except (ValidationError, BusinessRuleError) as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("customers.show", customer_id=c.id))


@bp.post("/<int:customer_id>/loyalty-adjust")
@require("loyalty.manage")
def loyalty_adjust(customer_id):
    from app.core.errors import ValidationError
    from app.services import loyalty

    c = svc.get_or_404(customer_id)
    try:
        loyalty.adjust(g.user, c, request.form.get("points"), request.form.get("reason", ""))
        db.session.commit()
        flash("Loyalty points adjusted.", "success")
    except ValidationError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("customers.show", customer_id=c.id))
