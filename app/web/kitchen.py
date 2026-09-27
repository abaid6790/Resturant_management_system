from __future__ import annotations

from flask import Blueprint, Response, current_app, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import BusinessRuleError, ValidationError
from app.core.events import stream
from app.extensions import db
from app.services import branches as branch_svc
from app.services import kitchen as svc
from app.services import pos as pos_svc

bp = Blueprint("kitchen", __name__, url_prefix="/kitchen")


# ---- stations --------------------------------------------------------------------------------

@bp.get("/stations")
@require("kitchen.manage")
def stations():
    return render_template("kitchen/stations.html", stations=svc.visible_stations(g.user))


@bp.route("/stations/new", methods=["GET", "POST"])
@require("kitchen.manage")
def new_station():
    errors = {}
    if request.method == "POST":
        try:
            st = svc.save_station(g.user, request.form.to_dict())
            db.session.commit()
            flash(f"Station {st.name} created.", "success")
            return redirect(url_for("kitchen.stations"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("kitchen/station_form.html", station=None, errors=errors, form=request.form,
                           branches=branch_svc.visible_branches(g.user, include_inactive=False)), \
        (422 if errors else 200)


@bp.route("/stations/<int:station_id>/edit", methods=["GET", "POST"])
@require("kitchen.manage")
def edit_station(station_id):
    st = svc.get_station_or_404(g.user, station_id)
    form, errors = {"name": st.name}, {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            svc.save_station(g.user, form, st)
            db.session.commit()
            flash("Station saved.", "success")
            return redirect(url_for("kitchen.stations"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("kitchen/station_form.html", station=st, errors=errors, form=form), \
        (422 if errors else 200)


@bp.post("/stations/<int:station_id>/status")
@require("kitchen.manage")
def station_status(station_id):
    st = svc.get_station_or_404(g.user, station_id)
    svc.set_station_active(g.user, st, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("kitchen.stations"))


# ---- firing (called from the order screen) -----------------------------------------------------

@bp.post("/orders/<int:order_id>/fire")
@require("kitchen.view")
def fire(order_id):
    order = pos_svc.get_order_or_404(g.user, order_id)
    try:
        tickets = svc.fire_order(g.user, order, priority=request.form.get("priority", "normal"))
        db.session.commit()
        flash(f"Sent {len(tickets)} ticket(s) to the kitchen.", "success")
    except BusinessRuleError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("orders.show", order_id=order.id))


# ---- the display ------------------------------------------------------------------------------

@bp.get("/board")
@require("kitchen.view")
def board():
    station_id = request.args.get("station_id", type=int)
    tickets = svc.visible_tickets(g.user, station_id=station_id)
    stations = svc.visible_stations(g.user, include_inactive=False)
    template = "kitchen/_board.html" if request.args.get("fragment") else "kitchen/board.html"
    return render_template(template, tickets=tickets, stations=stations, station_id=station_id)


@bp.get("/stream")
@require("kitchen.view")
def sse_stream():
    branch_id = g.branch.id if g.branch else None
    bus = current_app.extensions["kitchen_events"]
    return Response(stream(bus, branch_id), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.post("/tickets/<int:ticket_id>/advance")
@require("kitchen.update")
def advance(ticket_id):
    ticket = svc.get_ticket_or_404(g.user, ticket_id)
    try:
        svc.advance(g.user, ticket, request.form.get("to_status"))
        db.session.commit()
    except BusinessRuleError as err:
        db.session.rollback()
        flash(err.message, "error")
    if request.form.get("from") == "board":
        return redirect(url_for("kitchen.board", station_id=request.form.get("station_id", type=int)))
    return redirect(url_for("orders.show", order_id=ticket.order_id))


@bp.post("/tickets/<int:ticket_id>/priority")
@require("kitchen.update")
def priority(ticket_id):
    ticket = svc.get_ticket_or_404(g.user, ticket_id)
    try:
        svc.set_priority(g.user, ticket, request.form.get("priority", "normal"))
        db.session.commit()
    except ValidationError as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("kitchen.board", station_id=request.form.get("station_id", type=int)))
