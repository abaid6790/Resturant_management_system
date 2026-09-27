"""
Kitchen stations, KOTs, and the kitchen display queue. Firing an order groups its unfired lines
by station (one ticket per station per fire, so the grill only ever sees grill tickets) and
publishes an SSE event so any open kitchen display refreshes. Ticket status only moves forward
(queued -> preparing -> ready -> served); voiding the order cancels any tickets still open.
"""
from __future__ import annotations

from datetime import UTC, datetime

from flask import current_app

from app.core.authz import allowed_branch_ids
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.extensions import db
from app.models.kitchen import (
    NEXT_STATUS,
    TICKET_PRIORITIES,
    KitchenStation,
    KitchenTicket,
    KitchenTicketLine,
)
from app.models.pos import Order, OrderLine
from app.services import audit
from app.services import settings as settings_svc


def _publish(branch_id: int) -> None:
    bus = current_app.extensions.get("kitchen_events")
    if bus:
        bus.publish(branch_id, "update")


# ============================================================================ stations ===========

def visible_stations(actor, branch_id: int | None = None, include_inactive=True):
    stmt = db.select(KitchenStation).order_by(KitchenStation.name)
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(KitchenStation.branch_id.in_(ids))
    if branch_id:
        stmt = stmt.where(KitchenStation.branch_id == branch_id)
    if not include_inactive:
        stmt = stmt.where(KitchenStation.is_active.is_(True))
    return list(db.session.scalars(stmt))


def get_station_or_404(actor, station_id: int) -> KitchenStation:
    ids = allowed_branch_ids(actor)
    s = db.session.get(KitchenStation, station_id)
    if s is None or (ids is not None and s.branch_id not in ids):
        raise NotFoundError("Kitchen station not found.")
    return s


def save_station(actor, data: dict, existing: KitchenStation | None = None) -> KitchenStation:
    from app.core.authz import ensure_branch_access
    from app.models.auth import Branch
    errors = {}
    if existing is not None:
        branch_id = existing.branch_id  # fixed once created; the edit form doesn't offer it
    else:
        branch_id = int(data.get("branch_id")) if str(data.get("branch_id") or "").isdigit() else None
        if branch_id is None or db.session.get(Branch, branch_id) is None:
            errors["branch_id"] = "Choose a branch."
        elif allowed_branch_ids(actor) is not None:
            try:
                ensure_branch_access(branch_id, actor)
            except Exception:  # noqa: BLE001
                errors["branch_id"] = "You do not have access to that branch."
    name = (data.get("name") or "").strip()
    if not name or len(name) > 60:
        errors["name"] = "Enter a station name."
    if "branch_id" not in errors and "name" not in errors:
        clash = db.session.scalar(db.select(KitchenStation).where(
            KitchenStation.branch_id == branch_id, db.func.lower(KitchenStation.name) == name.lower()))
        if clash and (existing is None or clash.id != existing.id):
            errors["name"] = "This branch already has a station with that name."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    if existing is None:
        st = KitchenStation(branch_id=branch_id, name=name, is_active=True)
        db.session.add(st)
        db.session.flush()
        audit.log("station.create", "kitchen", record_type="station", record_id=st.id,
                  branch_id=branch_id, after={"name": name})
        return st
    existing.name = name
    audit.log("station.update", "kitchen", record_type="station", record_id=existing.id,
             branch_id=existing.branch_id, after={"name": name})
    return existing


def set_station_active(actor, st: KitchenStation, active: bool) -> None:
    if st.is_active != active:
        st.is_active = active
        audit.log("station.activate" if active else "station.deactivate", "kitchen",
                  record_type="station", record_id=st.id, branch_id=st.branch_id)


# ============================================================================ firing =============

def _next_ticket_number() -> str:
    prefix = settings_svc.get("kitchen.ticket_number_prefix") or "KOT"
    n = (db.session.scalar(db.select(db.func.count(KitchenTicket.id))) or 0) + 1
    while db.session.scalar(db.select(KitchenTicket.id)
                            .where(KitchenTicket.ticket_number == f"{prefix}-{n:06d}")):
        n += 1
    return f"{prefix}-{n:06d}"


def fire_lines(actor, order: Order, lines: list[OrderLine], *, priority: str = "normal"
              ) -> list[KitchenTicket]:
    """Group the given (unfired) lines by station and create one ticket per station."""
    if priority not in TICKET_PRIORITIES:
        priority = "normal"
    unfired = [ln for ln in lines if not ln.is_fired]
    if not unfired:
        return []
    by_station: dict[int | None, list[OrderLine]] = {}
    for ln in unfired:
        by_station.setdefault(ln.item.station_id, []).append(ln)

    tickets = []
    for station_id, station_lines in by_station.items():
        ticket = KitchenTicket(ticket_number=_next_ticket_number(), order_id=order.id,
                               station_id=station_id, branch_id=order.branch_id, priority=priority,
                               fired_by=actor.id if actor else None)
        db.session.add(ticket)
        db.session.flush()
        for ln in station_lines:
            db.session.add(KitchenTicketLine(ticket_id=ticket.id, order_line_id=ln.id,
                                             quantity=ln.quantity, notes=ln.notes))
            ln.is_fired = True
        tickets.append(ticket)
    db.session.flush()
    audit.log("kitchen.fire", "kitchen", record_type="order", record_id=order.id,
             branch_id=order.branch_id, reference=order.order_number,
             after={"tickets": [t.ticket_number for t in tickets]})
    _publish(order.branch_id)
    return tickets


def fire_order(actor, order, *, priority: str = "normal") -> list[KitchenTicket]:
    if order.status != "open":
        raise BusinessRuleError("This order is no longer open.")
    tickets = fire_lines(actor, order, order.lines, priority=priority)
    if not tickets:
        raise BusinessRuleError("Everything on this order has already been sent to the kitchen.")
    return tickets


def cancel_tickets_for_order(order) -> None:
    """Called when an order is voided: any ticket not already served is cancelled."""
    tickets = db.session.scalars(
        db.select(KitchenTicket).where(KitchenTicket.order_id == order.id,
                                       KitchenTicket.status.notin_(("served", "cancelled")))
    ).all()
    for t in tickets:
        t.status = "cancelled"
    if tickets:
        _publish(order.branch_id)


# ============================================================================ the board ==========

def visible_tickets(actor, *, station_id: int | None = None, statuses=("queued", "preparing", "ready")):
    stmt = (db.select(KitchenTicket).where(KitchenTicket.status.in_(statuses))
           .order_by(KitchenTicket.priority.desc(), KitchenTicket.fired_at))
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(KitchenTicket.branch_id.in_(ids))
    if station_id:
        stmt = stmt.where(KitchenTicket.station_id == station_id)
    return list(db.session.scalars(stmt))


def get_ticket_or_404(actor, ticket_id: int) -> KitchenTicket:
    ids = allowed_branch_ids(actor)
    t = db.session.get(KitchenTicket, ticket_id)
    if t is None or (ids is not None and t.branch_id not in ids):
        raise NotFoundError("Kitchen ticket not found.")
    return t


def advance(actor, ticket: KitchenTicket, to_status: str | None = None) -> KitchenTicket:
    """Move a ticket to the next status, or to `to_status` if given (still must be the next one)."""
    if ticket.status in ("served", "cancelled"):
        raise BusinessRuleError("This ticket is already closed.")
    expected_next = NEXT_STATUS.get(ticket.status)
    if expected_next is None or (to_status and to_status != expected_next):
        raise BusinessRuleError(f"A ticket in '{ticket.status}' cannot move to '{to_status}'.")
    now = datetime.now(UTC)
    ticket.status = expected_next
    if expected_next == "preparing":
        ticket.started_at = now
    elif expected_next == "ready":
        ticket.ready_at = now
    elif expected_next == "served":
        ticket.served_at = now
    audit.log("kitchen.advance", "kitchen", record_type="ticket", record_id=ticket.id,
             branch_id=ticket.branch_id, reference=ticket.ticket_number,
             after={"status": ticket.status})
    _publish(ticket.branch_id)
    return ticket


def set_priority(actor, ticket: KitchenTicket, priority: str) -> None:
    if priority not in TICKET_PRIORITIES:
        raise ValidationError("Invalid priority.")
    if ticket.priority != priority:
        ticket.priority = priority
        _publish(ticket.branch_id)
