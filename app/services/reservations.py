"""Reservations. Seating one starts a dine-in order at the assigned table, using the same
start_order() every walk-in uses, and links back to the reservation."""
from __future__ import annotations

from datetime import datetime

from app.core.authz import allowed_branch_ids
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.extensions import db
from app.models.pos import Customer, Reservation, Table
from app.services import audit

NEXT_STATUS = {"requested": "confirmed", "confirmed": "seated"}


def visible_stmt(actor, *, on_date=None, status=None):
    stmt = db.select(Reservation).order_by(Reservation.reserved_at)
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Reservation.branch_id.in_(ids))
    if on_date:
        stmt = stmt.where(db.func.date(Reservation.reserved_at) == on_date)
    if status:
        stmt = stmt.where(Reservation.status == status)
    return stmt


def get_or_404(actor, reservation_id: int) -> Reservation:
    ids = allowed_branch_ids(actor)
    r = db.session.get(Reservation, reservation_id)
    if r is None or (ids is not None and r.branch_id not in ids):
        raise NotFoundError("Reservation not found.")
    return r


def create(actor, data: dict) -> Reservation:
    from app.core.authz import ensure_branch_access
    from app.models.auth import Branch
    errors = {}
    branch_id = int(data.get("branch_id")) if str(data.get("branch_id") or "").isdigit() else None
    if branch_id is None or db.session.get(Branch, branch_id) is None:
        errors["branch_id"] = "Choose a branch."
    elif allowed_branch_ids(actor) is not None:
        try:
            ensure_branch_access(branch_id, actor)
        except Exception:  # noqa: BLE001
            errors["branch_id"] = "You do not have access to that branch."

    customer_id = None
    guest_name = (data.get("guest_name") or "").strip()
    if data.get("customer_id"):
        customer = db.session.get(Customer, int(data["customer_id"]))
        if customer is None or not customer.is_active:
            errors["customer_id"] = "Choose a valid customer."
        else:
            customer_id, guest_name = customer.id, customer.name
    elif not guest_name:
        errors["guest_name"] = "Enter a name, or choose an existing customer."

    try:
        party_size = int(data.get("party_size"))
        assert party_size > 0
    except Exception:  # noqa: BLE001
        errors["party_size"] = "Enter the number of guests."
        party_size = None

    reserved_at = None
    raw_dt = (data.get("reserved_at") or "").strip()
    if raw_dt:
        try:
            reserved_at = datetime.fromisoformat(raw_dt)
        except ValueError:
            errors["reserved_at"] = "Enter a valid date and time."
    else:
        errors["reserved_at"] = "Choose a date and time."

    table_id = None
    if data.get("table_id"):
        table = db.session.get(Table, int(data["table_id"]))
        if table is None or table.branch_id != branch_id:
            errors["table_id"] = "Choose a valid table."
        else:
            table_id = table.id

    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    r = Reservation(branch_id=branch_id, customer_id=customer_id, guest_name=guest_name[:120],
                    guest_phone=(data.get("guest_phone") or "").strip()[:40] or None,
                    party_size=party_size, reserved_at=reserved_at, table_id=table_id,
                    status="requested", notes=(data.get("notes") or "").strip()[:255] or None,
                    created_by=actor.id if actor else None)
    db.session.add(r)
    db.session.flush()
    audit.log("reservation.create", "pos", record_type="reservation", record_id=r.id,
             branch_id=branch_id, after={"name": guest_name, "party_size": party_size})
    return r


def advance(actor, r: Reservation) -> Reservation:
    if r.status not in NEXT_STATUS:
        raise BusinessRuleError(f"A reservation in '{r.status}' cannot be advanced here.")
    r.status = NEXT_STATUS[r.status]
    audit.log("reservation.advance", "pos", record_type="reservation", record_id=r.id,
             branch_id=r.branch_id, after={"status": r.status})
    return r


def cancel(actor, r: Reservation, reason: str | None = None) -> None:
    if r.status in ("seated", "completed", "cancelled", "no_show"):
        raise BusinessRuleError(f"A reservation in '{r.status}' cannot be cancelled.")
    r.status, r.notes = "cancelled", (reason or r.notes)
    audit.log("reservation.cancel", "pos", record_type="reservation", record_id=r.id,
             branch_id=r.branch_id)


def mark_no_show(actor, r: Reservation) -> None:
    if r.status not in ("requested", "confirmed"):
        raise BusinessRuleError(f"A reservation in '{r.status}' cannot be marked no-show.")
    r.status = "no_show"
    audit.log("reservation.no_show", "pos", record_type="reservation", record_id=r.id,
             branch_id=r.branch_id)


def seat(actor, r: Reservation, *, location_id=None):
    from app.models.inventory import Location
    from app.services import pos as pos_svc

    if r.status not in ("requested", "confirmed"):
        raise BusinessRuleError(f"A reservation in '{r.status}' cannot be seated.")
    if r.table_id is None:
        raise ValidationError("Assign a table before seating this reservation.",
                              details={"table_id": "Choose a table."})
    if location_id is None:
        loc = db.session.scalar(db.select(Location).where(Location.branch_id == r.branch_id,
                                                           Location.is_active.is_(True))
                                .order_by(Location.id))
        if loc is None:
            raise BusinessRuleError("This branch has no active location to serve from.")
        location_id = loc.id
    if r.table.status != "available":
        raise BusinessRuleError(f"{r.table.name} is not available.")
    order = pos_svc.start_order(actor, {"order_type": "dine_in", "location_id": location_id,
                                        "table_id": r.table_id, "customer_id": r.customer_id,
                                        "guest_count": r.party_size})
    r.status, r.order_id = "seated", order.id
    audit.log("reservation.seat", "pos", record_type="reservation", record_id=r.id,
             branch_id=r.branch_id, after={"order": order.order_number})
    return order
