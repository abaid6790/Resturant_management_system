from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.dates import as_date
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.http import paginate
from app.extensions import db
from app.models.auth import User
from app.services import branches as branch_svc
from app.services import hr as svc

bp = Blueprint("hr", __name__, url_prefix="/hr")


# ---- employees ----------------------------------------------------------------------------------

@bp.get("/employees")
@require("employees.view")
def employees():
    q = request.args.get("q", "")
    rows, meta = paginate(svc.visible_stmt(g.user, q=q))
    return render_template("hr/employees.html", employees=rows, meta=meta, q=q)


def _unlinked_users():
    from app.models.hr import Employee
    linked = db.select(Employee.user_id).where(Employee.user_id.isnot(None))
    return list(db.session.scalars(db.select(User).where(~User.id.in_(linked)).order_by(User.full_name)))


@bp.route("/employees/new", methods=["GET", "POST"])
@require("employees.manage")
def new_employee():
    errors = {}
    if request.method == "POST":
        try:
            e = svc.create(g.user, request.form.to_dict())
            db.session.commit()
            flash(f"Added {e.full_name}.", "success")
            return redirect(url_for("hr.show_employee", employee_id=e.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("hr/employee_form.html", employee=None, errors=errors, form=request.form,
                           branches=branch_svc.visible_branches(g.user, include_inactive=False),
                           users=_unlinked_users()), (422 if errors else 200)


@bp.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
@require("employees.manage")
def edit_employee(employee_id):
    e = svc.get_or_404(g.user, employee_id)
    form = {"full_name": e.full_name, "position": e.position or "", "phone": e.phone or "",
            "email": e.email or "", "hire_date": e.hire_date.isoformat() if e.hire_date else "",
            "notes": e.notes or "", "user_id": str(e.user_id or "")}
    errors = {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            svc.update(g.user, e, form)
            db.session.commit()
            flash("Employee saved.", "success")
            return redirect(url_for("hr.show_employee", employee_id=e.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    users = _unlinked_users() + ([e.user] if e.user else [])
    return render_template("hr/employee_form.html", employee=e, errors=errors, form=form,
                           branches=[e.branch], users=users), (422 if errors else 200)


@bp.get("/employees/<int:employee_id>")
@require("employees.view")
def show_employee(employee_id):
    e = svc.get_or_404(g.user, employee_id)
    open_rec = svc.open_attendance_for(e.id)
    records, meta = paginate(svc.attendance_stmt(g.user, employee_id=e.id))
    return render_template("hr/employee_show.html", employee=e, open_record=open_rec,
                           records=records, meta=meta)


@bp.post("/employees/<int:employee_id>/status")
@require("employees.manage")
def employee_status(employee_id):
    e = svc.get_or_404(g.user, employee_id)
    svc.set_active(g.user, e, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("hr.employees"))


# ---- attendance -----------------------------------------------------------------------------------

@bp.get("/attendance")
@require("attendance.view")
def attendance():
    a = request.args
    records, meta = paginate(svc.attendance_stmt(g.user, date_from=a.get("from") or None,
                                                 date_to=a.get("to") or None))
    return render_template("hr/attendance.html", records=records, meta=meta, f=a)


@bp.post("/employees/<int:employee_id>/clock-in")
@require("attendance.manage")
def clock_in(employee_id):
    e = svc.get_or_404(g.user, employee_id)
    try:
        svc.clock_in(g.user, e)
        db.session.commit()
        flash(f"{e.full_name} clocked in.", "success")
    except BusinessRuleError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("hr.show_employee", employee_id=e.id))


@bp.post("/attendance/<int:record_id>/clock-out")
@require("attendance.manage")
def clock_out(record_id):
    from app.models.hr import AttendanceRecord
    rec = db.session.get(AttendanceRecord, record_id)
    if rec is None:
        raise NotFoundError("Attendance record not found.")
    try:
        svc.clock_out(g.user, rec)
        db.session.commit()
        flash("Clocked out.", "success")
    except BusinessRuleError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("hr.show_employee", employee_id=rec.employee_id))


# ---- shifts ------------------------------------------------------------------------------------

@bp.get("/shifts")
@require("shifts.manage")
def shifts():
    parsed = as_date(request.args.get("date"))
    on_date = parsed.isoformat() if parsed else None
    rows = svc.visible_shifts(g.user, include_inactive=False)
    assignments = (list(db.session.scalars(svc.assignments_stmt(g.user, date_from=on_date,
                                                                date_to=on_date))) if on_date else [])
    return render_template("hr/shifts.html", shifts=rows, assignments=assignments, on_date=on_date)


@bp.route("/shifts/new", methods=["GET", "POST"])
@require("shifts.manage")
def new_shift():
    errors = {}
    if request.method == "POST":
        try:
            svc.save_shift(g.user, request.form.to_dict())
            db.session.commit()
            flash("Shift created.", "success")
            return redirect(url_for("hr.shifts"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("hr/shift_form.html", shift=None, errors=errors, form=request.form,
                           branches=branch_svc.visible_branches(g.user, include_inactive=False)), \
        (422 if errors else 200)


@bp.post("/shifts/<int:shift_id>/status")
@require("shifts.manage")
def shift_status(shift_id):
    s = svc.get_shift_or_404(g.user, shift_id)
    svc.set_shift_active(g.user, s, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("hr.shifts"))


@bp.post("/shifts/<int:shift_id>/assign")
@require("shifts.manage")
def assign(shift_id):
    s = svc.get_shift_or_404(g.user, shift_id)
    try:
        employee = svc.get_or_404(g.user, int(request.form.get("employee_id", 0)))
        svc.assign(g.user, s, employee, request.form.get("work_date"),
                  request.form.get("notes") or None)
        db.session.commit()
        flash("Assigned.", "success")
    except (ValidationError, BusinessRuleError) as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("hr.shifts", date=request.form.get("work_date")))


@bp.post("/assignments/<int:assignment_id>/status")
@require("shifts.manage")
def assignment_status(assignment_id):
    from app.models.hr import ShiftAssignment
    a = db.session.get(ShiftAssignment, assignment_id)
    if a is None:
        raise NotFoundError("Assignment not found.")
    try:
        svc.set_assignment_status(g.user, a, request.form.get("status"))
        db.session.commit()
    except ValidationError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("hr.shifts", date=a.work_date.isoformat()))
