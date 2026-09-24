from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import BusinessRuleError, ValidationError
from app.core.http import paginate
from app.extensions import db
from app.models.inventory import MOVEMENT_TYPES, InventoryItem, StockBalance, StockBatch, StockMovement
from app.services import catalog as catalog_svc
from app.services import inventory as inv_svc

bp = Blueprint("stock", __name__, url_prefix="/inventory/stock")


def _locations():
    return catalog_svc.visible_locations(g.user, include_inactive=False)


def _items():
    return list(db.session.scalars(inv_svc.db.select(InventoryItem)
                                   .where(InventoryItem.is_active.is_(True)).order_by(InventoryItem.name)))


@bp.get("/")
@require("inventory.view")
def overview():
    location_id = request.args.get("location_id", type=int)
    locs = _locations()
    if location_id and location_id not in {loc.id for loc in locs}:
        location_id = None
    stmt = db.select(StockBalance).join(InventoryItem).where(InventoryItem.is_active.is_(True))
    ids = {loc.id for loc in locs}
    stmt = stmt.where(StockBalance.location_id.in_(ids)) if ids else stmt.where(False)
    if location_id:
        stmt = stmt.where(StockBalance.location_id == location_id)
    stmt = stmt.order_by(InventoryItem.name)
    rows, meta = paginate(stmt)
    can_costs = g.user.has_permission("inventory.view_costs")
    return render_template("stock/overview.html", rows=rows, meta=meta, locations=locs,
                           location_id=location_id, can_costs=can_costs,
                           cost=inv_svc.current_unit_cost if can_costs else None)


@bp.get("/ledger")
@require("inventory.view")
def ledger():
    from app.core.authz import allowed_branch_ids
    a = request.args
    stmt = db.select(StockMovement).order_by(StockMovement.id.desc())
    ids = allowed_branch_ids(g.user)
    if ids is not None:
        stmt = stmt.where(StockMovement.branch_id.in_(ids))
    item_id = a.get("item_id", type=int)
    if item_id:
        stmt = stmt.where(StockMovement.item_id == item_id)
    location_id = a.get("location_id", type=int)
    if location_id:
        stmt = stmt.where(StockMovement.location_id == location_id)
    if a.get("movement_type"):
        stmt = stmt.where(StockMovement.movement_type == a["movement_type"])
    entries, meta = paginate(stmt)
    return render_template("stock/ledger.html", entries=entries, meta=meta, items=_items(),
                           locations=_locations(), movement_types=MOVEMENT_TYPES, f=a,
                           can_costs=g.user.has_permission("inventory.view_costs"))


@bp.route("/adjust", methods=["GET", "POST"])
@require("inventory.adjust")
def adjust():
    errors = {}
    if request.method == "POST":
        f = request.form
        try:
            item = inv_svc.get_item_or_404(g.user, int(f.get("item_id", 0)))
            loc = inv_svc.get_location_or_404(g.user, int(f.get("location_id", 0)))
            from app.core.money import D
            delta_raw = f.get("delta", "0")
            try:
                delta = D(delta_raw)
            except Exception as exc:  # noqa: BLE001
                raise ValidationError("Enter a valid quantity.",
                                      details={"delta": "Enter a valid number."}) from exc
            cost = f.get("unit_cost") or None
            inv_svc.adjust_stock(item=item, location=loc, delta=delta, reason=f.get("reason", ""),
                                actor=g.user, unit_cost=(D(cost) if cost else None))
            db.session.commit()
            flash(f"Adjusted {item.name} at {loc.name} by {delta} {item.stock_unit.code}.", "success")
            return redirect(url_for("stock.ledger", item_id=item.id))
        except (ValidationError, BusinessRuleError) as err:
            db.session.rollback()
            errors = (err.details if isinstance(err, ValidationError) and err.details
                     else {"delta": err.message})
            flash(err.message, "error")
    return render_template("stock/adjust.html", items=_items(), locations=_locations(),
                           errors=errors, form=request.form), (422 if errors else 200)


@bp.route("/transfer", methods=["GET", "POST"])
@require("inventory.transfer")
def transfer():
    errors = {}
    if request.method == "POST":
        f = request.form
        try:
            item = inv_svc.get_item_or_404(g.user, int(f.get("item_id", 0)))
            src = inv_svc.get_location_or_404(g.user, int(f.get("from_location_id", 0)))
            dst = inv_svc.get_location_or_404(g.user, int(f.get("to_location_id", 0)))
            from app.core.money import D
            qty = D(f.get("quantity", "0"))
            inv_svc.transfer_stock(item=item, from_location=src, to_location=dst, quantity=qty,
                                  actor=g.user, reason=f.get("reason") or None)
            db.session.commit()
            flash(f"Transferred {qty} {item.stock_unit.code} of {item.name}.", "success")
            return redirect(url_for("stock.ledger", item_id=item.id))
        except (ValidationError, BusinessRuleError) as err:
            db.session.rollback()
            errors = (err.details if isinstance(err, ValidationError) and err.details
                     else {"quantity": err.message})
            flash(err.message, "error")
    return render_template("stock/transfer.html", items=_items(), locations=_locations(),
                           errors=errors, form=request.form), (422 if errors else 200)


@bp.route("/wastage", methods=["GET", "POST"])
@require("inventory.wastage")
def wastage():
    errors = {}
    if request.method == "POST":
        f = request.form
        try:
            item = inv_svc.get_item_or_404(g.user, int(f.get("item_id", 0)))
            loc = inv_svc.get_location_or_404(g.user, int(f.get("location_id", 0)))
            from app.core.money import D
            qty = D(f.get("quantity", "0"))
            inv_svc.record_wastage(item=item, location=loc, quantity=qty, reason=f.get("reason", ""),
                                  actor=g.user)
            db.session.commit()
            flash(f"Recorded wastage of {qty} {item.stock_unit.code} of {item.name}.", "success")
            return redirect(url_for("stock.ledger", item_id=item.id))
        except (ValidationError, BusinessRuleError) as err:
            db.session.rollback()
            errors = (err.details if isinstance(err, ValidationError) and err.details
                     else {"quantity": err.message})
            flash(err.message, "error")
    return render_template("stock/wastage.html", items=_items(), locations=_locations(),
                           errors=errors, form=request.form), (422 if errors else 200)


@bp.get("/batches")
@require("inventory.view")
def batches():
    from app.core.authz import allowed_branch_ids
    from app.models.inventory import Location
    a = request.args
    stmt = db.select(StockBatch).join(Location).where(StockBatch.qty_remaining > 0)
    ids = allowed_branch_ids(g.user)
    if ids is not None:
        stmt = stmt.where(Location.branch_id.in_(ids))
    if a.get("expiring") == "1":
        stmt = stmt.where(StockBatch.expiry_date.isnot(None)).order_by(StockBatch.expiry_date)
    else:
        stmt = stmt.order_by(StockBatch.received_at)
    item_id = a.get("item_id", type=int)
    if item_id:
        stmt = stmt.where(StockBatch.item_id == item_id)
    rows, meta = paginate(stmt)
    return render_template("stock/batches.html", rows=rows, meta=meta, items=_items(), f=a)
