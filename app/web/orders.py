from __future__ import annotations

from itertools import zip_longest

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import BusinessRuleError, PermissionDeniedError, ValidationError
from app.core.http import paginate
from app.extensions import db
from app.models.inventory import InventoryItem
from app.models.pos import ORDER_TYPES, ProductModifier
from app.services import catalog as catalog_svc
from app.services import customers as customer_svc
from app.services import floors as floor_svc
from app.services import pos as svc

bp = Blueprint("orders", __name__, url_prefix="/pos/orders")


@bp.get("/")
@require("orders.view_all")
def index():
    status = request.args.get("status", "open")
    orders, meta = paginate(svc.visible_orders(g.user, status or None))
    return render_template("orders/list.html", orders=orders, meta=meta, status=status)


@bp.route("/new", methods=["GET", "POST"])
@require("pos.sell")
def new():
    errors = {}
    if request.method == "POST":
        try:
            order = svc.start_order(g.user, request.form.to_dict())
            db.session.commit()
            return redirect(url_for("orders.show", order_id=order.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    locations = catalog_svc.visible_locations(g.user, include_inactive=False)
    tables = [t for f in floor_svc.visible_floors(g.user, include_inactive=False) for t in f.tables
             if t.is_active and t.status == "available"]
    return render_template("orders/new.html", errors=errors, form=request.form, order_types=ORDER_TYPES,
                           locations=locations, tables=tables,
                           customers=list(db.session.scalars(customer_svc.search()))), \
        (422 if errors else 200)


def _sellable_items():
    return list(db.session.scalars(
        db.select(InventoryItem).where(InventoryItem.is_active.is_(True),
                                       InventoryItem.selling_price.isnot(None))
        .order_by(InventoryItem.name)
    ))


@bp.get("/<int:order_id>")
@require("pos.sell")
def show(order_id):
    order = svc.get_order_or_404(g.user, order_id)
    totals = svc.compute_totals(order)
    modifiers_by_item = {}
    for item in _sellable_items():
        mods = list(db.session.scalars(db.select(ProductModifier)
                                       .where(ProductModifier.item_id == item.id,
                                             ProductModifier.is_active.is_(True))))
        if mods:
            modifiers_by_item[item.id] = [{"id": m.id, "name": m.name, "price_delta": str(m.price_delta)}
                                          for m in mods]
    from app.services import settings as settings_svc
    return render_template("orders/show.html", order=order, totals=totals, items=_sellable_items(),
                           modifiers_by_item=modifiers_by_item,
                           customers=list(db.session.scalars(customer_svc.search())),
                           discount_limit=settings_svc.get("pos.discount_limit_pct"))


@bp.post("/<int:order_id>/lines")
@require("pos.sell")
def add_line(order_id):
    order = svc.get_order_or_404(g.user, order_id)
    f = request.form
    try:
        svc.add_line(g.user, order, {"item_id": f.get("item_id"), "quantity": f.get("quantity"),
                                     "notes": f.get("notes"), "unit_price": f.get("unit_price"),
                                     "modifier_ids": f.getlist("modifier_ids")})
        db.session.commit()
    except ValidationError as err:
        db.session.rollback()
        flash(err.message, "error")
    # PermissionDeniedError (e.g. price override without pos.price_override) is NOT caught here:
    # it propagates to the global handler as a 403, the same as every other permission check in
    # the app, rather than being silently swallowed into a redirect.
    return redirect(url_for("orders.show", order_id=order.id))


@bp.post("/<int:order_id>/lines/<int:line_id>/remove")
@require("pos.sell")
def remove_line(order_id, line_id):
    order = svc.get_order_or_404(g.user, order_id)
    line = next((ln for ln in order.lines if ln.id == line_id), None)
    if line:
        try:
            svc.remove_line(g.user, order, line)
            db.session.commit()
        except BusinessRuleError as err:
            db.session.rollback()
            flash(err.message, "error")
    return redirect(url_for("orders.show", order_id=order.id))


@bp.post("/<int:order_id>/customer")
@require("pos.sell")
def set_customer(order_id):
    order = svc.get_order_or_404(g.user, order_id)
    try:
        svc.set_customer(g.user, order, request.form.get("customer_id"))
        db.session.commit()
    except (ValidationError, BusinessRuleError) as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("orders.show", order_id=order.id))


@bp.post("/<int:order_id>/discount")
@require("pos.discount")
def set_discount(order_id):
    order = svc.get_order_or_404(g.user, order_id)
    f = request.form
    try:
        svc.set_discount(g.user, order, f.get("discount_kind", "none"), f.get("discount_value", "0"),
                         f.get("discount_reason", ""))
        db.session.commit()
        flash("Discount applied.", "success")
    except (ValidationError, BusinessRuleError, PermissionDeniedError) as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("orders.show", order_id=order.id))


@bp.post("/<int:order_id>/delivery-charge")
@require("pos.sell")
def set_delivery_charge(order_id):
    order = svc.get_order_or_404(g.user, order_id)
    try:
        svc.set_delivery_charge(g.user, order, request.form.get("delivery_charge", "0"))
        db.session.commit()
    except ValidationError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("orders.show", order_id=order.id))


@bp.post("/<int:order_id>/void")
@require("pos.void")
def void(order_id):
    order = svc.get_order_or_404(g.user, order_id)
    try:
        svc.void_order(g.user, order, request.form.get("reason", ""))
        db.session.commit()
        flash(f"Order {order.order_number} voided.", "success")
    except (ValidationError, BusinessRuleError) as err:
        db.session.rollback()
        flash(err.message, "error")
        return redirect(url_for("orders.show", order_id=order.id))
    return redirect(url_for("orders.index"))


@bp.route("/<int:order_id>/checkout", methods=["GET", "POST"])
@require("pos.sell")
def checkout(order_id):
    order = svc.get_order_or_404(g.user, order_id)
    totals = svc.compute_totals(order)
    errors, failed = {}, False
    if request.method == "POST":
        f = request.form
        payments = [{"method": m, "amount": a, "reference": r}
                   for m, a, r in zip_longest(f.getlist("pay_method"), f.getlist("pay_amount"),
                                              f.getlist("pay_reference")) if a]
        try:
            invoice = svc.complete_sale(g.user, order, payments)
            db.session.commit()
            flash(f"Sale completed: {invoice.invoice_number}.", "success")
            return redirect(url_for("orders.receipt", order_id=order.id))
        except (ValidationError, BusinessRuleError) as err:
            db.session.rollback()
            failed = True
            errors = err.details if isinstance(err, ValidationError) and err.details else {}
            flash(err.message, "error")
    return render_template("orders/checkout.html", order=order, totals=totals, errors=errors), \
        (422 if failed else 200)


@bp.get("/<int:order_id>/receipt")
@require("pos.sell")
def receipt(order_id):
    order = svc.get_order_or_404(g.user, order_id)
    if order.invoice is None:
        flash("This order has not been completed yet.", "error")
        return redirect(url_for("orders.show", order_id=order.id))
    return render_template("orders/receipt.html", order=order, invoice=order.invoice)


@bp.post("/<int:order_id>/reprint")
@require("pos.reprint")
def reprint(order_id):
    order = svc.get_order_or_404(g.user, order_id)
    if order.invoice:
        svc.reprint_invoice(g.user, order.invoice)
        db.session.commit()
    return redirect(url_for("orders.receipt", order_id=order.id))
