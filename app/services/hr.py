"""Employees, attendance, and shift scheduling."""
from __future__ import annotations

from datetime import UTC, datetime

from app.core.authz import allowed_branch_ids
from app.core.dates import as_date
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.extensions import db
from app.models.hr import AttendanceRecord, Employee, Shift, ShiftAssignment
from app.services import audit

# ============================================================================ employees =========

def visible_stmt(actor, *, q="", branch_id=None, active_only=True):
    stmt = db.select(Employee).order_by(Employee.full_name)
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Employee.branch_id.in_(ids))
    if branch_id:
        stmt = stmt.where(Employee.branch_id == branch_id)
    if active_only:
        stmt = stmt.where(Employee.is_active.is_(True))
    if q:
        stmt = stmt.where(Employee.full_name.ilike(f"%{q.strip()}%"))
    return stmt


def get_or_404(actor, employee_id: int) -> Employee:
    ids = allowed_branch_ids(actor)
    e = db.session.get(Employee, employee_id)
    if e is None or (ids is not None and e.branch_id not in ids):
        raise NotFoundError("Employee not found.")
    return e


def _clean(actor, data: dict, existing: Employee | None) -> dict:
    from app.core.authz import ensure_branch_access
    from app.models.auth import Branch, User
    errors, out = {}, {}
    if existing is not None:
        branch_id = existing.branch_id  # fixed once created, same as Shift/KitchenStation
    else:
        branch_id = int(data.get("branch_id")) if str(data.get("branch_id") or "").isdigit() else None
        if branch_id is None or db.session.get(Branch, branch_id) is None:
            errors["branch_id"] = "Choose a branch."
        elif allowed_branch_ids(actor) is not None:
            try:
                ensure_branch_access(branch_id, actor)
            except Exception:  # noqa: BLE001
                errors["branch_id"] = "You do not have access to that branch."
    out["branch_id"] = branch_id
    out["full_name"] = (data.get("full_name") or "").strip()
    if not out["full_name"] or len(out["full_name"]) > 120:
        errors["full_name"] = "Enter the employee's name."
    out["position"] = (data.get("position") or "").strip()[:80] or None
    out["phone"] = (data.get("phone") or "").strip()[:40] or None
    email = (data.get("email") or "").strip()
    if email and ("@" not in email or len(email) > 255):
        errors["email"] = "Enter a valid email address."
    out["email"] = email or None
    out["hire_date"] = as_date(data.get("hire_date"))
    out["notes"] = (data.get("notes") or "").strip() or None
    user_id = None
    if data.get("user_id"):
        user = db.session.get(User, int(data["user_id"]))
        if user is None:
            errors["user_id"] = "Choose a valid user."
        else:
            clash = db.session.scalar(db.select(Employee).where(Employee.user_id == user.id))
            if clash and (existing is None or clash.id != existing.id):
                errors["user_id"] = "That user is already linked to another employee."
            user_id = user.id
    out["user_id"] = user_id
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    return out


def create(actor, data: dict) -> Employee:
    clean = _clean(actor, data, None)
    e = Employee(**clean, is_active=True)
    db.session.add(e)
    db.session.flush()
    audit.log("employee.create", "hr", record_type="employee", record_id=e.id,
             branch_id=e.branch_id, after={"name": e.full_name})
    return e


def update(actor, e: Employee, data: dict) -> Employee:
    clean = _clean(actor, data, e)
    for k, v in clean.items():
        setattr(e, k, v)
    audit.log("employee.update", "hr", record_type="employee", record_id=e.id, branch_id=e.branch_id,
             after={"name": e.full_name})
    return e


def set_active(actor, e: Employee, active: bool) -> None:
    if e.is_active != active:
        e.is_active = active
        audit.log("employee.activate" if active else "employee.deactivate", "hr",
                 record_type="employee", record_id=e.id, branch_id=e.branch_id)


# ============================================================================ attendance =========

def open_attendance_for(employee_id: int) -> AttendanceRecord | None:
    return db.session.scalar(
        db.select(AttendanceRecord).where(AttendanceRecord.employee_id == employee_id,
                                          AttendanceRecord.clock_out.is_(None))
    )


def clock_in(actor, employee: Employee) -> AttendanceRecord:
    if open_attendance_for(employee.id):
        raise BusinessRuleError(f"{employee.full_name} is already clocked in.")
    rec = AttendanceRecord(employee_id=employee.id, branch_id=employee.branch_id,
                           clock_in=datetime.now(UTC), recorded_by=actor.id if actor else None)
    db.session.add(rec)
    db.session.flush()
    audit.log("attendance.clock_in", "hr", record_type="attendance", record_id=rec.id,
             branch_id=employee.branch_id, after={"employee": employee.full_name})
    return rec


def clock_out(actor, rec: AttendanceRecord) -> AttendanceRecord:
    if rec.clock_out is not None:
        raise BusinessRuleError("This attendance record is already closed.")
    rec.clock_out = datetime.now(UTC)
    audit.log("attendance.clock_out", "hr", record_type="attendance", record_id=rec.id,
             branch_id=rec.branch_id)
    return rec


def attendance_stmt(actor, *, employee_id=None, branch_id=None, date_from=None, date_to=None):
    date_from, date_to = as_date(date_from), as_date(date_to)
    stmt = db.select(AttendanceRecord).order_by(AttendanceRecord.clock_in.desc())
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(AttendanceRecord.branch_id.in_(ids))
    if employee_id:
        stmt = stmt.where(AttendanceRecord.employee_id == employee_id)
    if branch_id:
        stmt = stmt.where(AttendanceRecord.branch_id == branch_id)
    if date_from:
        stmt = stmt.where(db.func.date(AttendanceRecord.clock_in) >= date_from)
    if date_to:
        stmt = stmt.where(db.func.date(AttendanceRecord.clock_in) <= date_to)
    return stmt


# ============================================================================ shifts =============

def visible_shifts(actor, branch_id=None, include_inactive=True):
    stmt = db.select(Shift).order_by(Shift.start_time)
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Shift.branch_id.in_(ids))
    if branch_id:
        stmt = stmt.where(Shift.branch_id == branch_id)
    if not include_inactive:
        stmt = stmt.where(Shift.is_active.is_(True))
    return list(db.session.scalars(stmt))


def get_shift_or_404(actor, shift_id: int) -> Shift:
    ids = allowed_branch_ids(actor)
    s = db.session.get(Shift, shift_id)
    if s is None or (ids is not None and s.branch_id not in ids):
        raise NotFoundError("Shift not found.")
    return s


def save_shift(actor, data: dict, existing: Shift | None = None) -> Shift:
    import datetime as dt

    from app.core.authz import ensure_branch_access
    from app.models.auth import Branch
    errors = {}
    if existing is not None:
        branch_id = existing.branch_id
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
        errors["name"] = "Enter a shift name."
    try:
        start = dt.time.fromisoformat(data.get("start_time"))
    except (ValueError, TypeError):
        errors["start_time"] = "Enter a valid start time."
        start = None
    try:
        end = dt.time.fromisoformat(data.get("end_time"))
    except (ValueError, TypeError):
        errors["end_time"] = "Enter a valid end time."
        end = None
    if "name" not in errors and "branch_id" not in errors:
        clash = db.session.scalar(db.select(Shift).where(Shift.branch_id == branch_id,
                                                          db.func.lower(Shift.name) == name.lower()))
        if clash and (existing is None or clash.id != existing.id):
            errors["name"] = "This branch already has a shift with that name."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    if existing is None:
        s = Shift(branch_id=branch_id, name=name, start_time=start, end_time=end, is_active=True)
        db.session.add(s)
        db.session.flush()
        audit.log("shift.create", "hr", record_type="shift", record_id=s.id, branch_id=branch_id,
                 after={"name": name})
        return s
    existing.name, existing.start_time, existing.end_time = name, start, end
    audit.log("shift.update", "hr", record_type="shift", record_id=existing.id,
             branch_id=existing.branch_id, after={"name": name})
    return existing


def set_shift_active(actor, s: Shift, active: bool) -> None:
    if s.is_active != active:
        s.is_active = active
        audit.log("shift.activate" if active else "shift.deactivate", "hr", record_type="shift",
                 record_id=s.id, branch_id=s.branch_id)


def assign(actor, shift: Shift, employee: Employee, work_date, notes=None) -> ShiftAssignment:
    parsed = as_date(work_date)
    if parsed is None:
        raise ValidationError("Enter a valid date.", details={"work_date": "Enter a valid date."})
    work_date = parsed
    if employee.branch_id != shift.branch_id:
        raise ValidationError("The employee and shift must be in the same branch.",
                              details={"employee_id": "Must match the shift's branch."})
    clash = db.session.scalar(db.select(ShiftAssignment).where(
        ShiftAssignment.shift_id == shift.id, ShiftAssignment.employee_id == employee.id,
        ShiftAssignment.work_date == work_date))
    if clash:
        raise ValidationError("This employee is already assigned to this shift on this date.",
                              details={"work_date": "Already assigned."})
    a = ShiftAssignment(shift_id=shift.id, employee_id=employee.id, work_date=work_date,
                        notes=notes, created_by=actor.id if actor else None)
    db.session.add(a)
    db.session.flush()
    audit.log("shift.assign", "hr", record_type="shift_assignment", record_id=a.id,
             branch_id=shift.branch_id, after={"employee": employee.full_name, "date": str(work_date)})
    return a


def assignments_stmt(actor, *, branch_id=None, employee_id=None, date_from=None, date_to=None):
    date_from, date_to = as_date(date_from), as_date(date_to)
    stmt = (db.select(ShiftAssignment).join(Shift)
           .order_by(ShiftAssignment.work_date, ShiftAssignment.id))
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Shift.branch_id.in_(ids))
    if branch_id:
        stmt = stmt.where(Shift.branch_id == branch_id)
    if employee_id:
        stmt = stmt.where(ShiftAssignment.employee_id == employee_id)
    if date_from:
        stmt = stmt.where(ShiftAssignment.work_date >= date_from)
    if date_to:
        stmt = stmt.where(ShiftAssignment.work_date <= date_to)
    return stmt


def set_assignment_status(actor, a: ShiftAssignment, status: str) -> None:
    from app.models.hr import SHIFT_ASSIGNMENT_STATUSES
    if status not in SHIFT_ASSIGNMENT_STATUSES:
        raise ValidationError("Invalid status.")
    a.status = status
    audit.log("shift.assignment_status", "hr", record_type="shift_assignment", record_id=a.id,
             branch_id=a.shift.branch_id, after={"status": status})
