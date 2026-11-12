from __future__ import annotations

import datetime as dt

from flask import Blueprint, g, render_template, request

from app.core.authz import require
from app.core.dates import as_date
from app.services import branches as branch_svc
from app.services import reporting as svc

bp = Blueprint("reports", __name__, url_prefix="/reports")


def _date_args(default_days=30):
    today = dt.date.today()
    date_from = as_date(request.args.get("from")) or today - dt.timedelta(days=default_days)
    date_to = as_date(request.args.get("to")) or today
    return date_from, date_to


@bp.get("/profit-and-loss")
@require("reports.financial")
def profit_and_loss():
    date_from, date_to = _date_args()
    branch_id = request.args.get("branch_id", type=int)
    pl = svc.profit_and_loss(g.user, branch_id=branch_id, date_from=date_from, date_to=date_to)
    return render_template("reports/profit_and_loss.html", pl=pl, date_from=date_from, date_to=date_to,
                           branch_id=branch_id, branches=branch_svc.visible_branches(g.user))


@bp.get("/daily-closing")
@require("reports.view")
def daily_closing():
    on_date = as_date(request.args.get("date")) or dt.date.today()
    branch_id = request.args.get("branch_id", type=int)
    data = svc.daily_closing(g.user, branch_id=branch_id, on_date=on_date)
    return render_template("reports/daily_closing.html", on_date=on_date, branch_id=branch_id,
                           branches=branch_svc.visible_branches(g.user), **data)


@bp.get("/employee-sales")
@require("reports.view")
def employee_sales():
    date_from, date_to = _date_args()
    branch_id = request.args.get("branch_id", type=int)
    rows = svc.sales_by_employee(g.user, branch_id=branch_id, date_from=date_from, date_to=date_to)
    return render_template("reports/employee_sales.html", rows=rows, date_from=date_from,
                           date_to=date_to, branch_id=branch_id,
                           branches=branch_svc.visible_branches(g.user))
