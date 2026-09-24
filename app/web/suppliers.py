from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import ValidationError
from app.core.http import paginate
from app.extensions import db
from app.services import purchasing as svc

bp = Blueprint("suppliers", __name__, url_prefix="/purchasing/suppliers")


@bp.get("/")
@require("suppliers.view")
def index():
    q = request.args.get("q", "")
    suppliers, meta = paginate(svc.search_suppliers(q))
    return render_template("suppliers/list.html", suppliers=suppliers, meta=meta, q=q,
                           balance=svc.supplier_balance)


def _read():
    return {k: request.form.get(k, "") for k in
            ("code", "name", "contact_name", "phone", "email", "address", "notes",
             "payment_terms_days")}


@bp.route("/new", methods=["GET", "POST"])
@require("suppliers.manage")
def new():
    form, errors = {}, {}
    if request.method == "POST":
        form = _read()
        try:
            s = svc.create_supplier(g.user, form)
            db.session.commit()
            flash(f"Created supplier {s.name}.", "success")
            return redirect(url_for("suppliers.show", supplier_id=s.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    status = 422 if errors else 200
    return render_template("suppliers/form.html", supplier=None, form=form, errors=errors), status


@bp.route("/<int:supplier_id>/edit", methods=["GET", "POST"])
@require("suppliers.manage")
def edit(supplier_id):
    s = svc.get_supplier_or_404(supplier_id)
    form = {"code": s.code, "name": s.name, "contact_name": s.contact_name or "",
            "phone": s.phone or "", "email": s.email or "", "address": s.address or "",
            "notes": s.notes or "", "payment_terms_days": str(s.payment_terms_days)}
    errors = {}
    if request.method == "POST":
        form = _read()
        try:
            svc.update_supplier(g.user, s, form)
            db.session.commit()
            flash("Supplier saved.", "success")
            return redirect(url_for("suppliers.show", supplier_id=s.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    status = 422 if errors else 200
    return render_template("suppliers/form.html", supplier=s, form=form, errors=errors), status


@bp.get("/<int:supplier_id>")
@require("suppliers.view")
def show(supplier_id):
    s = svc.get_supplier_or_404(supplier_id)
    entries, meta = paginate(svc.supplier_ledger_stmt(supplier_id))
    from app.models.purchasing import PurchaseOrder
    recent_pos = list(db.session.scalars(
        db.select(PurchaseOrder).where(PurchaseOrder.supplier_id == supplier_id)
        .order_by(PurchaseOrder.id.desc()).limit(10)
    ))
    return render_template("suppliers/show.html", supplier=s, balance=svc.supplier_balance(supplier_id),
                           entries=entries, meta=meta, recent_pos=recent_pos,
                           can_pay=g.user.has_permission("purchases.pay"))


@bp.post("/<int:supplier_id>/status")
@require("suppliers.manage")
def status(supplier_id):
    s = svc.get_supplier_or_404(supplier_id)
    svc.set_supplier_active(g.user, s, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("suppliers.index"))


@bp.route("/<int:supplier_id>/pay", methods=["POST"])
@require("purchases.pay")
def pay(supplier_id):
    s = svc.get_supplier_or_404(supplier_id)
    try:
        svc.record_payment(g.user, s, request.form.to_dict())
        db.session.commit()
        flash("Payment recorded.", "success")
    except ValidationError as err:
        db.session.rollback()
        flash((err.details or {}).get("amount") or err.message, "error")
    return redirect(url_for("suppliers.show", supplier_id=s.id))
