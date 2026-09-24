"""
The stock ledger. Every function here posts a StockMovement, updates StockBalance and (where
relevant) StockBatch rows inside the CALLER's transaction, so a business event that touches
several items commits or rolls back as one unit. Nothing here calls db.session.commit().

Consumption is always FIFO: oldest batch (by received_at) with qty_remaining > 0 first.
"""
from __future__ import annotations

from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.money import D
from app.core.money import qty as qround
from app.core.money import unit_cost as cround
from app.extensions import db
from app.models.inventory import InventoryItem, Location, StockBalance, StockBatch, StockMovement
from app.services import audit

ZERO = D(0)


def get_item_or_404(actor, item_id: int) -> InventoryItem:
    item = db.session.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("Item not found.")
    return item


def get_location_or_404(actor, location_id: int) -> Location:
    from app.core.authz import allowed_branch_ids
    loc = db.session.get(Location, location_id)
    ids = allowed_branch_ids(actor)
    if loc is None or (ids is not None and loc.branch_id not in ids):
        raise NotFoundError("Location not found.")
    return loc


def _balance_row(item_id: int, location_id: int) -> StockBalance:
    row = db.session.get(StockBalance, (item_id, location_id))
    if row is None:
        row = StockBalance(item_id=item_id, location_id=location_id, qty=ZERO)
        db.session.add(row)
        db.session.flush()
    return row


def current_balance(item_id: int, location_id: int):
    row = db.session.get(StockBalance, (item_id, location_id))
    return row.qty if row else ZERO


def _post(*, item: InventoryItem, location: Location, movement_type: str, qty, unit_cost,
          batch_id, reference_type, reference_id, reason, actor,
          allow_negative: bool = False) -> StockMovement:
    """Write one ledger line and update the cached balance. `qty` is signed."""
    bal = _balance_row(item.id, location.id)
    previous = bal.qty
    new_balance = qround(D(previous) + D(qty))
    if new_balance < 0 and not allow_negative:
        raise BusinessRuleError(
            f"This would take {item.name} below zero at {location.name} "
            f"(have {previous}, requested change {qty})."
        )
    bal.qty = new_balance
    mv = StockMovement(
        item_id=item.id, location_id=location.id, branch_id=location.branch_id, batch_id=batch_id,
        movement_type=movement_type, qty=qround(D(qty)), unit_cost=cround(unit_cost),
        previous_balance=previous, new_balance=new_balance, reference_type=reference_type,
        reference_id=None if reference_id is None else str(reference_id), reason=reason,
        user_id=actor.id if actor else None,
    )
    db.session.add(mv)
    db.session.flush()
    return mv


def receive_stock(*, item: InventoryItem, location: Location, quantity, unit_cost, actor,
                  batch_no: str | None = None, expiry_date=None, source_type: str = "opening",
                  source_id=None, movement_type: str = "opening", reference_type=None,
                  reference_id=None, reason=None) -> StockMovement:
    """Stock coming IN with a cost: opening stock, adjustments in, purchases (Phase 3), transfers in."""
    quantity = qround(quantity)
    if quantity <= 0:
        raise ValidationError("Quantity received must be greater than zero.")
    batch = None
    if item.track_batches:
        batch = StockBatch(
            item_id=item.id, location_id=location.id, batch_no=batch_no or f"{item.sku}-{location.id}",
            unit_cost=cround(unit_cost), qty_received=quantity, qty_remaining=quantity,
            expiry_date=expiry_date, source_type=source_type, source_id=source_id,
        )
        db.session.add(batch)
        db.session.flush()
    mv = _post(item=item, location=location, movement_type=movement_type, qty=quantity,
              unit_cost=unit_cost, batch_id=batch.id if batch else None,
              reference_type=reference_type, reference_id=reference_id, reason=reason, actor=actor)
    audit.log(f"stock.{movement_type}", "inventory", record_type="stock_movement", record_id=mv.id,
             branch_id=location.branch_id, reference=reference_type,
             after={"item": item.sku, "location": location.name, "qty": str(quantity),
                    "unit_cost": str(cround(unit_cost)), "batch": batch.batch_no if batch else None})
    return mv


def _fifo_layers(item_id: int, location_id: int):
    return db.session.scalars(
        db.select(StockBatch)
        .where(StockBatch.item_id == item_id, StockBatch.location_id == location_id,
              StockBatch.qty_remaining > 0)
        .order_by(StockBatch.received_at, StockBatch.id)
        .with_for_update(of=StockBatch)  # lock only the batch rows, not the outer-joined lookups
    )


def consume_stock(*, item: InventoryItem, location: Location, quantity, actor,
                  movement_type: str = "consumption", reference_type=None, reference_id=None,
                  reason=None, allow_negative: bool | None = None) -> list[StockMovement]:
    """
    Stock going OUT, consumed oldest-batch-first. Returns one StockMovement per batch touched
    (a sale can draw from several cost layers). For items that don't track batches, the item's
    last known unit cost is reused and the whole quantity posts as a single movement.
    """
    quantity = qround(quantity)
    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    if allow_negative is None:
        from app.services.settings import get as _get_setting
        allow_negative = bool(_get_setting("inventory.allow_negative_stock"))

    if not item.track_batches:
        last = db.session.scalar(
            db.select(StockMovement.unit_cost).where(StockMovement.item_id == item.id)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(1)
        ) or ZERO
        if not allow_negative and current_balance(item.id, location.id) < quantity:
            raise BusinessRuleError(f"Not enough {item.name} at {location.name} to do this.")
        mv = _post(item=item, location=location, movement_type=movement_type, qty=-quantity,
                  unit_cost=last, batch_id=None, reference_type=reference_type,
                  reference_id=reference_id, reason=reason, actor=actor,
                  allow_negative=allow_negative)
        return [mv]

    remaining = quantity
    movements: list[StockMovement] = []
    for batch in _fifo_layers(item.id, location.id):
        if remaining <= 0:
            break
        take = min(batch.qty_remaining, remaining)
        batch.qty_remaining = qround(D(batch.qty_remaining) - take)
        mv = _post(item=item, location=location, movement_type=movement_type, qty=-take,
                  unit_cost=batch.unit_cost, batch_id=batch.id, reference_type=reference_type,
                  reference_id=reference_id, reason=reason, actor=actor)
        movements.append(mv)
        remaining = qround(D(remaining) - take)

    if remaining > 0:
        if not allow_negative:
            raise BusinessRuleError(
                f"Not enough {item.name} at {location.name}: short by {remaining} "
                f"{item.stock_unit.code}."
            )
        mv = _post(item=item, location=location, movement_type=movement_type, qty=-remaining,
                  unit_cost=ZERO, batch_id=None, reference_type=reference_type,
                  reference_id=reference_id, reason="stock went negative: " + (reason or ""),
                  actor=actor, allow_negative=True)
        movements.append(mv)

    audit.log(f"stock.{movement_type}", "inventory", record_type="stock_movement",
             record_id=movements[-1].id if movements else None, branch_id=location.branch_id,
             reference=reference_type, after={"item": item.sku, "location": location.name,
                                              "qty": str(-quantity), "layers": len(movements)})
    return movements


def adjust_stock(*, item, location, delta, reason: str, actor, unit_cost=None):
    """Manual correction. Positive delta receives new stock in; negative consumes FIFO."""
    delta = qround(delta)
    if delta == 0:
        raise ValidationError("Enter a non-zero quantity.")
    if not reason or not reason.strip():
        raise ValidationError("A reason is required for stock adjustments.")
    if delta > 0:
        cost = unit_cost if unit_cost is not None else _current_unit_cost(item.id, location.id)
        return [receive_stock(item=item, location=location, quantity=delta, unit_cost=cost,
                              actor=actor, source_type="adjustment", movement_type="adjustment_in",
                              reference_type="adjustment", reason=reason)]
    return consume_stock(item=item, location=location, quantity=-delta, actor=actor,
                        movement_type="adjustment_out", reference_type="adjustment", reason=reason)


def record_wastage(*, item, location, quantity, reason: str, actor):
    if not reason or not reason.strip():
        raise ValidationError("A reason is required for wastage.")
    return consume_stock(item=item, location=location, quantity=quantity, actor=actor,
                        movement_type="wastage", reference_type="wastage", reason=reason)


def transfer_stock(*, item, from_location, to_location, quantity, actor, reason=None):
    """Atomic: FIFO-consume at the source, receive at the destination at the SAME cost(s)."""
    if from_location.id == to_location.id:
        raise ValidationError("Source and destination must be different.")
    out_moves = consume_stock(item=item, location=from_location, quantity=quantity, actor=actor,
                             movement_type="transfer_out", reference_type="transfer", reason=reason)
    in_moves = []
    for m in out_moves:
        in_moves.append(receive_stock(
            item=item, location=to_location, quantity=-m.qty, unit_cost=m.unit_cost, actor=actor,
            source_type="transfer", movement_type="transfer_in", reference_type="transfer",
            reference_id=m.id, reason=reason,
            batch_no=f"XFER-{m.id}" if item.track_batches else None,
        ))
    return out_moves, in_moves


def _current_unit_cost(item_id: int, location_id: int):
    """Weighted-average cost of stock currently on hand (used as a display/costing figure)."""
    row = db.session.execute(
        db.select(db.func.sum(StockBatch.qty_remaining * StockBatch.unit_cost),
                 db.func.sum(StockBatch.qty_remaining))
        .where(StockBatch.item_id == item_id, StockBatch.location_id == location_id,
              StockBatch.qty_remaining > 0)
    ).one()
    total_value, total_qty = row
    if not total_qty:
        last = db.session.scalar(
            db.select(StockMovement.unit_cost).where(StockMovement.item_id == item_id)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(1)
        )
        return cround(last) if last is not None else ZERO
    return cround(D(total_value) / D(total_qty))


def current_unit_cost(item, location):
    return _current_unit_cost(item.id, location.id)


def stock_valuation(location_id: int | None = None):
    """Sum of qty_remaining * unit_cost across batches (optionally one location)."""
    stmt = db.select(db.func.coalesce(db.func.sum(StockBatch.qty_remaining * StockBatch.unit_cost), 0))
    if location_id:
        stmt = stmt.where(StockBatch.location_id == location_id)
    return cround(db.session.scalar(stmt) or ZERO)
