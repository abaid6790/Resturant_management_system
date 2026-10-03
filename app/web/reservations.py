from __future__ import annotations

import datetime as dt

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import BusinessRuleError, ValidationError
from app.extensions import db
from app.services import branches as branch_svc
from app.services import customers as customer_svc
from app.services import floors as floor_svc
from app.services import reservations as svc

bp = Blueprint("reservations", __name__, url_prefix="/pos/reservations")


@bp.get("/")
@require("reservations.view")
def index():
    raw_date = request.args.get("date")
    try:
        on_date = dt.date.fromisoformat(raw_date) if raw_date else dt.date.today()
    except ValueError:
        on_date = dt.date.today()
    status = request.args.get("status", "")
    reservations = list(db.session.scalars(svc.visible_stmt(g.user, on_date=on_date, status=status or None)))
    return render_template("reservations/list.html", reservations=reservations, on_date=on_date,
                           status=status)


@bp.route("/new", methods=["GET", "POST"])
@require("reservations.manage")
def new():
    errors = {}
    if request.method == "POST":
        try:
            r = svc.create(g.user, request.form.to_dict())
            db.session.commit()
            flash(f"Reservation for {r.guest_name} created.", "success")
            return redirect(url_for("reservations.index", date=r.reserved_at.date().isoformat()))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("reservations/form.html", errors=errors, form=request.form,
                           branches=branch_svc.visible_branches(g.user, include_inactive=False),
                           tables=[t for f in floor_svc.visible_floors(g.user, include_inactive=False)
                                  for t in f.tables if t.is_active],
                           customers=list(db.session.scalars(customer_svc.search()))), \
        (422 if errors else 200)


def _action(fn, success, needs_reason=False):
    r = svc.get_or_404(g.user, request.view_args["reservation_id"])
    try:
        if needs_reason:
            fn(g.user, r, request.form.get("reason"))
        else:
            fn(g.user, r)
        db.session.commit()
        flash(success, "success")
    except BusinessRuleError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("reservations.index", date=r.reserved_at.date().isoformat()))


@bp.post("/<int:reservation_id>/advance")
@require("reservations.manage")
def advance(reservation_id):
    return _action(svc.advance, "Reservation updated.")


@bp.post("/<int:reservation_id>/cancel")
@require("reservations.manage")
def cancel(reservation_id):
    return _action(svc.cancel, "Reservation cancelled.", needs_reason=True)


@bp.post("/<int:reservation_id>/no-show")
@require("reservations.manage")
def no_show(reservation_id):
    return _action(svc.mark_no_show, "Marked as no-show.")


@bp.post("/<int:reservation_id>/seat")
@require("pos.sell")
def seat(reservation_id):
    r = svc.get_or_404(g.user, reservation_id)
    try:
        order = svc.seat(g.user, r, location_id=request.form.get("location_id") or None)
        db.session.commit()
        return redirect(url_for("orders.show", order_id=order.id))
    except (ValidationError, BusinessRuleError) as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("reservations.index", date=r.reserved_at.date().isoformat()))
