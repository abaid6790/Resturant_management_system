"""
Dashboard figures. Everything is a live database query scoped to the branches the user may see
(and optionally one chosen branch). "Live state" cards (active tables, pending orders, low
stock...) ignore the date range; "period" cards (sales, expenses, profit...) use it.
"""
from __future__ import annotations

import datetime as dt

from app.core.authz import allowed_branch_ids
from app.core.money import D, money
from app.extensions import db
from app.models.expenses import Expense
from app.models.inventory import InventoryItem, Location, StockBalance, StockBatch
from app.models.kitchen import KitchenTicket
from app.models.pos import (
    CashRegisterSession, CustomerLedgerEntry, Invoice, Order, OrderLine, Refund, RefundLine,
    Reservation, Table,
)
from app.models.purchasing import PurchaseInvoice, SupplierLedgerEntry
from app.services import cash as cash_svc
from app.services import reporting
from app.services import settings as settings_svc

_bf = reporting._branch_filter


def daily_series(actor, *, branch_id=None, date_from, date_to) -> list[dict]:
    """One row per calendar day in the range: revenue, cogs, expenses, profit (all net of refunds)."""
    days = {}
    d = date_from
    while d <= date_to:
        days[d] = {"date": d, "gross_sales": D(0), "refunds": D(0), "cogs": D(0),
                   "restocked": D(0), "expenses": D(0)}
        d += dt.timedelta(days=1)

    day = db.func.date(Invoice.created_at)
    q = _bf(db.select(day, db.func.sum(Invoice.total), db.func.sum(Invoice.cogs_total))
            .where(day >= date_from, day <= date_to).group_by(day), Invoice.branch_id, actor, branch_id)
    for d, total, cogs in db.session.execute(q):
        days[d]["gross_sales"], days[d]["cogs"] = D(total or 0), D(cogs or 0)

    rday = db.func.date(Refund.created_at)
    q = _bf(db.select(rday, db.func.sum(Refund.total_amount)).where(rday >= date_from, rday <= date_to)
            .group_by(rday), Refund.branch_id, actor, branch_id)
    for d, total in db.session.execute(q):
        days[d]["refunds"] = D(total or 0)

    q = _bf(db.select(rday, db.func.sum(RefundLine.restocked_cost)).join(Refund)
            .where(rday >= date_from, rday <= date_to).group_by(rday), Refund.branch_id, actor, branch_id)
    for d, total in db.session.execute(q):
        days[d]["restocked"] = D(total or 0)

    q = _bf(db.select(Expense.expense_date, db.func.sum(Expense.amount))
            .where(Expense.expense_date >= date_from, Expense.expense_date <= date_to)
            .group_by(Expense.expense_date), Expense.branch_id, actor, branch_id)
    for d, total in db.session.execute(q):
        days[d]["expenses"] = D(total or 0)

    out = []
    for d in sorted(days):
        r = days[d]
        net_sales = money(r["gross_sales"] - r["refunds"])
        cogs = money(r["cogs"] - r["restocked"])
        gross = money(net_sales - cogs)
        out.append({"date": d, "gross_sales": money(r["gross_sales"]), "refunds": money(r["refunds"]),
                    "net_sales": net_sales, "cogs": cogs, "gross_profit": gross,
                    "gross_margin_pct": money(gross / net_sales * 100) if net_sales > 0 else D("0.00"),
                    "expenses": money(r["expenses"]), "net_profit": money(gross - r["expenses"])})
    return out


def _count(stmt):
    return db.session.scalar(stmt) or 0


def live_state(actor, branch_id=None) -> dict:
    today = dt.date.today()
    warn_days = int(settings_svc.get("inventory.expiry_warning_days"))

    active_tables = _count(_bf(db.select(db.func.count(Table.id)).where(
        Table.is_active.is_(True), Table.status == "occupied"), Table.branch_id, actor, branch_id))
    pending_orders = _count(_bf(db.select(db.func.count(Order.id)).where(Order.status == "open"),
                                Order.branch_id, actor, branch_id))
    kitchen_orders = _count(_bf(db.select(db.func.count(KitchenTicket.id)).where(
        KitchenTicket.status.in_(("queued", "preparing"))), KitchenTicket.branch_id, actor, branch_id))
    reservations = _count(_bf(db.select(db.func.count(Reservation.id)).where(
        db.func.date(Reservation.reserved_at) == today,
        Reservation.status.in_(("requested", "confirmed"))), Reservation.branch_id, actor, branch_id))

    stock = (db.select(db.func.count()).select_from(StockBalance).join(InventoryItem)
             .join(Location, Location.id == StockBalance.location_id)
             .where(InventoryItem.is_active.is_(True)))
    low = _count(_bf(stock.where(StockBalance.qty > 0, InventoryItem.reorder_level > 0,
                                 StockBalance.qty <= InventoryItem.reorder_level),
                     Location.branch_id, actor, branch_id))
    out = _count(_bf(stock.where(StockBalance.qty <= 0), Location.branch_id, actor, branch_id))

    batches = (db.select(db.func.count()).select_from(StockBatch)
               .join(Location, Location.id == StockBatch.location_id)
               .where(StockBatch.qty_remaining > 0, StockBatch.expiry_date.isnot(None)))
    expiring = _count(_bf(batches.where(StockBatch.expiry_date >= today,
                                        StockBatch.expiry_date <= today + dt.timedelta(days=warn_days)),
                          Location.branch_id, actor, branch_id))
    expired = _count(_bf(batches.where(StockBatch.expiry_date < today), Location.branch_id, actor, branch_id))

    sessions = db.session.scalars(_bf(
        db.select(CashRegisterSession).where(CashRegisterSession.status == "open"),
        CashRegisterSession.branch_id, actor, branch_id)).all()
    cash_balance = money(sum((cash_svc.session_cash_total(s.id) for s in sessions), D(0)))

    cust = _bf(db.select(db.func.coalesce(db.func.sum(CustomerLedgerEntry.amount), 0)),
               CustomerLedgerEntry.branch_id, actor, branch_id)
    sup = _bf(db.select(db.func.coalesce(db.func.sum(SupplierLedgerEntry.amount), 0)),
              SupplierLedgerEntry.branch_id, actor, branch_id)
    return {"active_tables": active_tables, "pending_orders": pending_orders,
            "kitchen_orders": kitchen_orders, "reservations_today": reservations,
            "low_stock": low, "out_of_stock": out, "expiring": expiring, "expired": expired,
            "open_registers": len(sessions), "cash_balance": cash_balance,
            "customer_outstanding": money(db.session.scalar(cust) or 0),
            "supplier_outstanding": money(db.session.scalar(sup) or 0)}


def top_products(actor, *, branch_id=None, date_from, date_to, limit=5) -> list[dict]:
    day = db.func.date(Invoice.created_at)
    q = (db.select(InventoryItem.name, db.func.sum(OrderLine.quantity),
                   db.func.sum(OrderLine.final_net_amount))
         .join(OrderLine, OrderLine.item_id == InventoryItem.id)
         .join(Order, Order.id == OrderLine.order_id).join(Invoice, Invoice.order_id == Order.id)
         .where(day >= date_from, day <= date_to)
         .group_by(InventoryItem.name).order_by(db.func.sum(OrderLine.final_net_amount).desc())
         .limit(limit))
    q = _bf(q, Order.branch_id, actor, branch_id)
    return [{"name": n, "qty": D(qty or 0), "net": money(net or 0)} for n, qty, net in db.session.execute(q)]


def purchase_total(actor, *, branch_id=None, date_from, date_to):
    q = _bf(db.select(db.func.coalesce(db.func.sum(PurchaseInvoice.total), 0)).where(
        PurchaseInvoice.invoice_date >= date_from, PurchaseInvoice.invoice_date <= date_to),
        PurchaseInvoice.branch_id, actor, branch_id)
    return money(db.session.scalar(q) or 0)


def build(actor, *, branch_id=None, date_from=None, date_to=None, include_financial=True) -> dict:
    today = dt.date.today()
    date_from, date_to = date_from or today, date_to or today
    if branch_id is not None:
        ids = allowed_branch_ids(actor)
        if ids is not None and branch_id not in ids:
            branch_id = None
    pl = reporting.profit_and_loss(actor, branch_id=branch_id, date_from=date_from, date_to=date_to)
    trend_from = max(date_from, date_to - dt.timedelta(days=30))  # chart at most 31 days
    series = daily_series(actor, branch_id=branch_id, date_from=trend_from, date_to=date_to)
    peak = max([abs(r["net_sales"]) for r in series] + [abs(r["net_profit"]) for r in series] + [D(1)])
    for r in series:
        r["sales_h"] = int(abs(r["net_sales"]) / peak * 100)
        r["profit_h"] = int(abs(r["net_profit"]) / peak * 100)
    pl["food_cost_pct"] = (money(pl["cogs"] / pl["net_sales"] * 100) if pl["net_sales"] > 0 else D("0.00"))
    data = {"pl": pl, "live": live_state(actor, branch_id), "series": series,
            "top_products": top_products(actor, branch_id=branch_id, date_from=date_from, date_to=date_to),
            "payment_methods": reporting.payment_method_breakdown(
                actor, branch_id=branch_id, date_from=date_from, date_to=date_to),
            "purchases": purchase_total(actor, branch_id=branch_id, date_from=date_from, date_to=date_to),
            "date_from": date_from, "date_to": date_to, "branch_id": branch_id,
            "include_financial": include_financial}
    return data
