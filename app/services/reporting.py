"""
Profit and loss, and daily closing. Every figure here is a straightforward sum over the
transactional tables already built in earlier phases (invoices, refunds, expenses, cash
sessions) — there is no separate ledger to keep in sync; the report always reconciles with the
underlying data because it IS the underlying data, aggregated.

  Net sales    = sum(invoice.total) - sum(refund.total_amount)
  COGS         = sum(invoice.cogs_total) - sum(refund_line.restocked_cost)   [restocked items
                 are no longer a cost, since they went back into sellable stock]
  Gross profit = Net sales - COGS
  Net profit   = Gross profit - expenses for the period
"""
from __future__ import annotations

from app.core.authz import allowed_branch_ids
from app.core.money import D, money
from app.extensions import db
from app.models.expenses import Expense
from app.models.inventory import StockMovement
from app.models.pos import CashRegisterSession, Invoice, Payment, Refund, RefundLine


def _branch_filter(stmt, column, actor, branch_id):
    ids = allowed_branch_ids(actor)
    if branch_id:
        return stmt.where(column == branch_id)
    if ids is not None:
        return stmt.where(column.in_(ids))
    return stmt


def profit_and_loss(actor, *, branch_id=None, date_from, date_to):
    inv_stmt = _branch_filter(
        db.select(db.func.coalesce(db.func.sum(Invoice.total), 0),
                 db.func.coalesce(db.func.sum(Invoice.cogs_total), 0),
                 db.func.coalesce(db.func.sum(Invoice.discount_total), 0),
                 db.func.coalesce(db.func.sum(Invoice.tax_total), 0),
                 db.func.count(Invoice.id))
        .where(db.func.date(Invoice.created_at) >= date_from, db.func.date(Invoice.created_at) <= date_to),
        Invoice.branch_id, actor, branch_id)
    gross_sales, cogs, discounts, tax, order_count = db.session.execute(inv_stmt).one()

    ref_stmt = _branch_filter(
        db.select(db.func.coalesce(db.func.sum(Refund.total_amount), 0))
        .where(db.func.date(Refund.created_at) >= date_from, db.func.date(Refund.created_at) <= date_to),
        Refund.branch_id, actor, branch_id)
    refunds = db.session.scalar(ref_stmt) or 0

    restocked_stmt = (db.select(db.func.coalesce(db.func.sum(RefundLine.restocked_cost), 0))
                      .join(Refund).where(db.func.date(Refund.created_at) >= date_from,
                                         db.func.date(Refund.created_at) <= date_to))
    restocked_stmt = _branch_filter(restocked_stmt, Refund.branch_id, actor, branch_id)
    restocked_cost = db.session.scalar(restocked_stmt) or 0

    exp_stmt = _branch_filter(
        db.select(db.func.coalesce(db.func.sum(Expense.amount), 0))
        .where(Expense.expense_date >= date_from, Expense.expense_date <= date_to),
        Expense.branch_id, actor, branch_id)
    expenses = db.session.scalar(exp_stmt) or 0

    net_sales = money(D(gross_sales) - D(refunds))
    net_cogs = money(D(cogs) - D(restocked_cost))
    gross_profit = money(net_sales - net_cogs)
    net_profit = money(gross_profit - D(expenses))

    return {
        "gross_sales": money(gross_sales), "refunds": money(refunds), "net_sales": net_sales,
        "discounts": money(discounts), "tax": money(tax), "cogs": net_cogs,
        "gross_profit": gross_profit, "gross_margin_pct": (
            money(gross_profit / net_sales * 100) if net_sales > 0 else D("0.00")),
        "expenses": money(expenses), "net_profit": net_profit, "net_margin_pct": (
            money(net_profit / net_sales * 100) if net_sales > 0 else D("0.00")),
        "order_count": order_count or 0,
    }


def payment_method_breakdown(actor, *, branch_id=None, date_from, date_to):
    stmt = (db.select(Payment.method, db.func.sum(Payment.amount)).join(Invoice)
           .where(db.func.date(Invoice.created_at) >= date_from,
                 db.func.date(Invoice.created_at) <= date_to)
           .group_by(Payment.method).order_by(Payment.method))
    stmt = _branch_filter(stmt, Invoice.branch_id, actor, branch_id)
    return list(db.session.execute(stmt).all())


def daily_closing(actor, *, branch_id=None, on_date):
    pl = profit_and_loss(actor, branch_id=branch_id, date_from=on_date, date_to=on_date)
    methods = payment_method_breakdown(actor, branch_id=branch_id, date_from=on_date, date_to=on_date)

    sessions_stmt = db.select(CashRegisterSession).where(
        db.func.date(CashRegisterSession.opened_at) == on_date)
    sessions_stmt = _branch_filter(sessions_stmt, CashRegisterSession.branch_id, actor, branch_id)
    sessions = list(db.session.scalars(sessions_stmt))

    wastage_stmt = (db.select(db.func.coalesce(db.func.sum(StockMovement.qty * StockMovement.unit_cost), 0))
                    .where(StockMovement.movement_type == "wastage",
                          db.func.date(StockMovement.created_at) == on_date))
    wastage_stmt = _branch_filter(wastage_stmt, StockMovement.branch_id, actor, branch_id)
    wastage_cost = money(-(db.session.scalar(wastage_stmt) or 0))

    return {"pl": pl, "payment_methods": methods, "cash_sessions": sessions,
            "wastage_cost": wastage_cost}


def sales_by_employee(actor, *, branch_id=None, date_from, date_to):
    from app.models.pos import Order
    stmt = (db.select(Order.server_id, db.func.count(Invoice.id), db.func.sum(Invoice.total))
           .join(Invoice, Invoice.order_id == Order.id)
           .where(db.func.date(Invoice.created_at) >= date_from,
                 db.func.date(Invoice.created_at) <= date_to)
           .group_by(Order.server_id).order_by(db.func.sum(Invoice.total).desc()))
    stmt = _branch_filter(stmt, Order.branch_id, actor, branch_id)
    rows = db.session.execute(stmt).all()
    from app.models.auth import User
    out = []
    for server_id, count, total in rows:
        user = db.session.get(User, server_id) if server_id else None
        out.append({"user": user, "order_count": count, "total": money(total or 0)})
    return out
