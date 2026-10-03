from __future__ import annotations

from itertools import zip_longest

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import BusinessRuleError, ValidationError
from app.core.http import paginate
from app.extensions import db
from app.services import refunds as svc

bp = Blueprint("refunds", __name__, url_prefix="/pos/refunds")


@bp.get("/")
@require("pos.refund")
def index():
    refunds, meta = paginate(svc.visible_stmt(g.user))
    return render_template("refunds/list.html", refunds=refunds, meta=meta)


@bp.route("/invoices/<int:invoice_id>/new", methods=["GET", "POST"])
@require("pos.refund")
def new(invoice_id):
    invoice = svc.get_invoice_or_404(g.user, invoice_id)
    errors = {}
    if request.method == "POST":
        f = request.form
        line_ids, qtys = f.getlist("order_line_id"), f.getlist("quantity")
        lines = [{"order_line_id": i, "quantity": q, "restock": f.get(f"restock_{i}") == "on"}
                for i, q in zip_longest(line_ids, qtys) if i and q]
        methods, amounts, refs = (f.getlist("pay_method"), f.getlist("pay_amount"),
                                  f.getlist("pay_reference"))
        payments = [{"method": m, "amount": a, "reference": r}
                   for m, a, r in zip_longest(methods, amounts, refs) if a]
        try:
            refund = svc.create_refund(g.user, invoice, {"reason": f.get("reason"), "lines": lines,
                                       "extras_amount": f.get("extras_amount"), "payments": payments})
            db.session.commit()
            flash(f"{refund.refund_number} recorded.", "success")
            return redirect(url_for("refunds.show", refund_id=refund.id))
        except (ValidationError, BusinessRuleError) as err:
            db.session.rollback()
            errors = err.details if isinstance(err, ValidationError) and err.details else {}
            flash(err.message, "error")
    from app.services import settings as settings_svc
    return render_template("refunds/new.html", invoice=invoice, errors=errors, form=request.form,
                           refundable=svc.refundable_lines(invoice), can_restock=svc.can_restock,
                           restock_default=settings_svc.get("returns.restock_by_default")), \
        (422 if errors else 200)


@bp.get("/<int:refund_id>")
@require("pos.refund")
def show(refund_id):
    refund = svc.get_or_404(g.user, refund_id)
    return render_template("refunds/show.html", refund=refund)
