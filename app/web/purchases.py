from __future__ import annotations

from itertools import zip_longest

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import BusinessRuleError, ValidationError
from app.core.http import paginate
from app.extensions import db
from app.models.inventory import InventoryItem
from app.models.purchasing import PO_STATUSES
from app.services import catalog as catalog_svc
from app.services import purchasing as svc

bp = Blueprint("purchases", __name__, url_prefix="/purchasing/orders")


@bp.get("/")
@require("purchases.view")
def index():
    status = request.args.get("status", "")
    supplier_id = request.args.get("supplier_id", type=int)
    pos, meta = paginate(svc.visible_pos(g.user, status or None, supplier_id))
    return render_template("purchases/list.html", pos=pos, meta=meta, status=status,
                           supplier_id=supplier_id, statuses=PO_STATUSES,
                           suppliers=list(db.session.scalars(svc.search_suppliers())))


def _items():
    return list(db.session.scalars(db.select(InventoryItem).where(InventoryItem.is_active.is_(True))
                                   .order_by(InventoryItem.name)))


def _lines_from_form():
    f = request.form
    ids, qtys, costs, pkgs = (f.getlist("line_item_id"), f.getlist("line_qty"),
                              f.getlist("line_cost"), f.getlist("line_packaging_id"))
    # zip_longest: packaging_unit_id is only present on lines that used one, so that list is
    # often shorter (or empty) — plain zip() would silently truncate every line to match it.
    return [{"item_id": i, "quantity": q, "unit_cost": c, "packaging_unit_id": p or None}
            for i, q, c, p in zip_longest(ids, qtys, costs, pkgs) if i]


@bp.route("/new", methods=["GET", "POST"])
@require("purchases.create")
def new():
    errors, form_lines = {}, []
    if request.method == "POST":
        f = request.form
        try:
            po = svc.create_po(g.user, {"supplier_id": f.get("supplier_id"),
                               "location_id": f.get("location_id"), "expected_date": f.get("expected_date"),
                               "notes": f.get("notes"), "lines": _lines_from_form()})
            db.session.commit()
            flash(f"Created {po.po_number} as a draft.", "success")
            return redirect(url_for("purchases.show", po_id=po.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
            form_lines = _display_lines(_lines_from_form())
    return render_template("purchases/form.html", po=None, errors=errors, form=request.form,
                           lines=form_lines, items=_items(),
                           suppliers=list(db.session.scalars(svc.search_suppliers())),
                           locations=catalog_svc.visible_locations(g.user, include_inactive=False)), \
        (422 if errors else 200)


def _display_lines(raw):
    out = []
    for r in raw:
        item = db.session.get(InventoryItem, int(r["item_id"])) if str(r["item_id"]).isdigit() else None
        out.append({"item_id": r["item_id"], "quantity": r["quantity"], "unit_cost": r["unit_cost"],
                   "packaging_unit_id": r["packaging_unit_id"] or "",
                   "name": item.name if item else "?", "unit_code": item.stock_unit.code if item else "?"})
    return out


@bp.route("/<int:po_id>/edit", methods=["GET", "POST"])
@require("purchases.create")
def edit(po_id):
    po = svc.get_po_or_404(g.user, po_id)
    errors = {}
    form_lines = [{"item_id": line.item_id, "quantity": str(line.quantity_ordered),
                  "unit_cost": str(line.unit_cost), "packaging_unit_id": line.packaging_unit_id or "",
                  "name": line.item.name, "unit_code": line.item.stock_unit.code} for line in po.lines]
    if request.method == "POST":
        f = request.form
        try:
            svc.update_po(g.user, po, {"expected_date": f.get("expected_date"), "notes": f.get("notes"),
                                       "lines": _lines_from_form()})
            db.session.commit()
            flash("Purchase order saved.", "success")
            return redirect(url_for("purchases.show", po_id=po.id))
        except (ValidationError, BusinessRuleError) as err:
            db.session.rollback()
            errors = (err.details if isinstance(err, ValidationError) and err.details
                     else {"lines": err.message})
            flash(err.message, "error")
            form_lines = _display_lines(_lines_from_form())
    return render_template("purchases/form.html", po=po, errors=errors, form=request.form,
                           lines=form_lines, items=_items(), suppliers=[po.supplier],
                           locations=[po.location]), (422 if errors else 200)


@bp.get("/<int:po_id>")
@require("purchases.view")
def show(po_id):
    po = svc.get_po_or_404(g.user, po_id)
    from app.models.purchasing import PurchaseInvoice, PurchaseReceipt
    receipts = list(db.session.scalars(db.select(PurchaseReceipt).where(PurchaseReceipt.po_id == po.id)
                                       .order_by(PurchaseReceipt.id.desc())))
    invoices = list(db.session.scalars(db.select(PurchaseInvoice).where(PurchaseInvoice.po_id == po.id)))
    from app.services import settings as settings_svc
    return render_template("purchases/show.html", po=po, receipts=receipts, invoices=invoices,
                           require_approval=settings_svc.get("purchasing.require_approval"))


def _action(fn, success, *, needs_reason=False):
    po = svc.get_po_or_404(g.user, request.view_args["po_id"])
    try:
        if needs_reason:
            fn(g.user, po, request.form.get("reason", ""))
        else:
            fn(g.user, po)
        db.session.commit()
        flash(success, "success")
    except (BusinessRuleError, ValidationError) as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("purchases.show", po_id=po.id))


@bp.post("/<int:po_id>/submit")
@require("purchases.create")
def submit(po_id):
    return _action(svc.submit_po, "Purchase order submitted.")


@bp.post("/<int:po_id>/approve")
@require("purchases.approve")
def approve(po_id):
    return _action(svc.approve_po, "Purchase order approved.")


@bp.post("/<int:po_id>/reject")
@require("purchases.approve")
def reject(po_id):
    return _action(svc.reject_po, "Purchase order rejected.", needs_reason=True)


@bp.post("/<int:po_id>/cancel")
@require("purchases.create")
def cancel(po_id):
    return _action(svc.cancel_po, "Purchase order cancelled.", needs_reason=True)


@bp.post("/<int:po_id>/receive")
@require("purchases.receive")
def receive(po_id):
    po = svc.get_po_or_404(g.user, po_id)
    f = request.form
    lines = [{"po_line_id": i, "quantity": q, "unit_cost": c, "batch_no": b, "expiry_date": e or None}
             for i, q, c, b, e in zip_longest(f.getlist("recv_line_id"), f.getlist("recv_qty"),
                                              f.getlist("recv_cost"), f.getlist("recv_batch"),
                                              f.getlist("recv_expiry")) if i and q]
    try:
        svc.receive_po(g.user, po, lines, reference=f.get("reference") or None)
        db.session.commit()
        flash("Stock received.", "success")
    except (BusinessRuleError, ValidationError) as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("purchases.show", po_id=po.id))


@bp.post("/<int:po_id>/invoice")
@require("purchases.pay")
def invoice(po_id):
    po = svc.get_po_or_404(g.user, po_id)
    try:
        svc.create_invoice(g.user, po, request.form.to_dict())
        db.session.commit()
        flash("Invoice recorded.", "success")
    except ValidationError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("purchases.show", po_id=po.id))


@bp.route("/returns/new", methods=["GET", "POST"])
@require("purchases.return")
def new_return():
    errors = {}
    if request.method == "POST":
        f = request.form
        lines = [{"item_id": i, "quantity": q, "unit_cost": c}
                 for i, q, c in zip_longest(f.getlist("ret_item_id"), f.getlist("ret_qty"),
                                            f.getlist("ret_cost")) if i]
        try:
            ret = svc.create_return(g.user, {"supplier_id": f.get("supplier_id"),
                                    "location_id": f.get("location_id"), "reason": f.get("reason"),
                                    "po_id": f.get("po_id") or None, "lines": lines})
            db.session.commit()
            flash(f"Return #{ret.id} recorded.", "success")
            return redirect(url_for("suppliers.show", supplier_id=ret.supplier_id))
        except (ValidationError, BusinessRuleError) as err:
            db.session.rollback()
            errors = err.details if isinstance(err, ValidationError) and err.details else {}
            flash(err.message, "error")
    return render_template("purchases/return_form.html", errors=errors, form=request.form,
                           items=_items(), suppliers=list(db.session.scalars(svc.search_suppliers())),
                           locations=catalog_svc.visible_locations(g.user, include_inactive=False)), \
        (422 if errors else 200)
