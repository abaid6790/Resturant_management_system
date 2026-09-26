from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import BusinessRuleError, ValidationError
from app.core.http import paginate
from app.extensions import db
from app.services import cash as svc
from app.services import catalog as catalog_svc

bp = Blueprint("cash", __name__, url_prefix="/pos/cash")


@bp.get("/")
@require("cash.session")
def index():
    location_id = request.args.get("location_id", type=int)
    status = request.args.get("status", "")
    sessions, meta = paginate(svc.visible_sessions(g.user, location_id, status or None))
    return render_template("cash/list.html", sessions=sessions, meta=meta,
                           locations=catalog_svc.visible_locations(g.user, include_inactive=False),
                           location_id=location_id, status=status)


@bp.route("/open", methods=["GET", "POST"])
@require("cash.session")
def open_session():
    errors, failed = {}, False
    if request.method == "POST":
        try:
            loc = catalog_svc.get_location_or_404(g.user, int(request.form.get("location_id", 0)))
            sess = svc.open_session(g.user, loc, request.form.get("opening_float", "0"))
            db.session.commit()
            flash(f"Cash register opened at {loc.name}.", "success")
            return redirect(url_for("cash.show", session_id=sess.id))
        except (ValidationError, BusinessRuleError) as err:
            db.session.rollback()
            failed = True
            errors = err.details if isinstance(err, ValidationError) and err.details else {}
            flash(err.message, "error")
    return render_template("cash/open.html", errors=errors, form=request.form,
                           locations=catalog_svc.visible_locations(g.user, include_inactive=False)), \
        (422 if failed else 200)


@bp.get("/<int:session_id>")
@require("cash.session")
def show(session_id):
    sess = svc.get_session_or_404(g.user, session_id)
    from app.models.pos import CashMovement
    movements = list(db.session.scalars(db.select(CashMovement).where(CashMovement.session_id == sess.id)
                                        .order_by(CashMovement.id)))
    running_total = svc.session_cash_total(sess.id) if sess.status == "open" else sess.expected_cash
    return render_template("cash/show.html", session=sess, movements=movements, running_total=running_total)


@bp.post("/<int:session_id>/cash-in")
@require("cash.manage")
def cash_in(session_id):
    sess = svc.get_session_or_404(g.user, session_id)
    try:
        svc.cash_in(g.user, sess, request.form.get("amount", "0"), request.form.get("reason", ""))
        db.session.commit()
        flash("Cash in recorded.", "success")
    except (ValidationError, BusinessRuleError) as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("cash.show", session_id=sess.id))


@bp.post("/<int:session_id>/cash-out")
@require("cash.manage")
def cash_out(session_id):
    sess = svc.get_session_or_404(g.user, session_id)
    try:
        svc.cash_out(g.user, sess, request.form.get("amount", "0"), request.form.get("reason", ""))
        db.session.commit()
        flash("Cash out recorded.", "success")
    except (ValidationError, BusinessRuleError) as err:
        db.session.rollback()
        flash(err.message, "error")
    return redirect(url_for("cash.show", session_id=sess.id))


@bp.route("/<int:session_id>/close", methods=["GET", "POST"])
@require("cash.session")
def close_session(session_id):
    sess = svc.get_session_or_404(g.user, session_id)
    errors, failed = {}, False
    if request.method == "POST":
        try:
            svc.close_session(g.user, sess, request.form.get("counted_cash", "0"))
            db.session.commit()
            flash("Cash register closed.", "success")
            return redirect(url_for("cash.show", session_id=sess.id))
        except (ValidationError, BusinessRuleError) as err:
            db.session.rollback()
            failed = True
            errors = err.details if isinstance(err, ValidationError) and err.details else {}
            flash(err.message, "error")
    return render_template("cash/close.html", session=sess, errors=errors,
                           expected=svc.session_cash_total(sess.id)), (422 if failed else 200)
