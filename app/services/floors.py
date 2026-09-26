"""Floors, tables, and table status. Table status also changes automatically from pos.py
(occupied when an order opens on it, available when that order completes, is voided, or the
table is cleared) — the transitions here are for manual/administrative changes only."""
from __future__ import annotations

from app.core.authz import allowed_branch_ids
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.extensions import db
from app.models.pos import TABLE_STATUSES, Floor, Table
from app.services import audit


def visible_floors(actor, branch_id: int | None = None, include_inactive=True):
    stmt = db.select(Floor).order_by(Floor.name)
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Floor.branch_id.in_(ids))
    if branch_id:
        stmt = stmt.where(Floor.branch_id == branch_id)
    if not include_inactive:
        stmt = stmt.where(Floor.is_active.is_(True))
    return list(db.session.scalars(stmt))


def get_floor_or_404(actor, floor_id: int) -> Floor:
    ids = allowed_branch_ids(actor)
    f = db.session.get(Floor, floor_id)
    if f is None or (ids is not None and f.branch_id not in ids):
        raise NotFoundError("Floor not found.")
    return f


def get_table_or_404(actor, table_id: int) -> Table:
    ids = allowed_branch_ids(actor)
    t = db.session.get(Table, table_id)
    if t is None or (ids is not None and t.branch_id not in ids):
        raise NotFoundError("Table not found.")
    return t


def save_floor(actor, data: dict, existing: Floor | None = None) -> Floor:
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
    name = (data.get("name") or "").strip()
    if not name or len(name) > 60:
        errors["name"] = "Enter a floor name."
    if "branch_id" not in errors and "name" not in errors:
        clash = db.session.scalar(db.select(Floor).where(Floor.branch_id == branch_id,
                                                          db.func.lower(Floor.name) == name.lower()))
        if clash and (existing is None or clash.id != existing.id):
            errors["name"] = "This branch already has a floor with that name."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    if existing is None:
        f = Floor(branch_id=branch_id, name=name, is_active=True)
        db.session.add(f)
        db.session.flush()
        audit.log("floor.create", "pos", record_type="floor", record_id=f.id, branch_id=branch_id,
                  after={"name": name})
        return f
    existing.name = name
    audit.log("floor.update", "pos", record_type="floor", record_id=existing.id,
             branch_id=existing.branch_id, after={"name": name})
    return existing


def save_table(actor, floor: Table, data: dict, existing: Table | None = None) -> Table:
    errors = {}
    name = (data.get("name") or "").strip()
    if not name or len(name) > 40:
        errors["name"] = "Enter a table name or number."
    capacity_raw = (data.get("capacity") or "2").strip()
    if not capacity_raw.isdigit() or int(capacity_raw) < 1:
        errors["capacity"] = "Enter a whole number of seats."
    if "name" not in errors:
        clash = db.session.scalar(db.select(Table).where(Table.floor_id == floor.id,
                                                          db.func.lower(Table.name) == name.lower()))
        if clash and (existing is None or clash.id != existing.id):
            errors["name"] = "This floor already has a table with that name."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    capacity = int(capacity_raw)
    if existing is None:
        t = Table(floor_id=floor.id, branch_id=floor.branch_id, name=name, capacity=capacity,
                 status="available", is_active=True)
        db.session.add(t)
        db.session.flush()
        audit.log("table.create", "pos", record_type="table", record_id=t.id, branch_id=floor.branch_id,
                  after={"name": name, "capacity": capacity})
        return t
    existing.name, existing.capacity = name, capacity
    audit.log("table.update", "pos", record_type="table", record_id=existing.id,
             branch_id=existing.branch_id, after={"name": name, "capacity": capacity})
    return existing


def set_floor_active(actor, f: Floor, active: bool) -> None:
    if f.is_active != active:
        f.is_active = active
        audit.log("floor.activate" if active else "floor.deactivate", "pos", record_type="floor",
                  record_id=f.id, branch_id=f.branch_id)


def set_table_active(actor, t: Table, active: bool) -> None:
    if not active and t.status == "occupied":
        raise BusinessRuleError("This table has an open order and cannot be deactivated.")
    if t.is_active != active:
        t.is_active = active
        audit.log("table.activate" if active else "table.deactivate", "pos", record_type="table",
                  record_id=t.id, branch_id=t.branch_id)


def set_table_status(actor, t: Table, status: str) -> None:
    if status not in TABLE_STATUSES:
        raise ValidationError("Invalid table status.")
    if t.status == "occupied" and status != "occupied":
        raise BusinessRuleError("This table has an open order; complete or void it first.")
    if t.status != status:
        t.status = status
        audit.log("table.status", "pos", record_type="table", record_id=t.id, branch_id=t.branch_id,
                  after={"status": status})
