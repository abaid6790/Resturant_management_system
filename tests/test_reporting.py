"""Profit and loss. The original spec's exit test: profit must reconcile with the transactions."""
import datetime as dt

from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryItem
from app.services import expenses as expense_svc
from app.services import pos as pos_svc
from app.services import refunds as refund_svc
from app.services import reporting as svc
from app.services import settings as settings_svc


def _actor(app):
    from app.models.auth import User
    u = db.session.scalar(db.select(User).where(User.username == "actor"))
    if u is None:
        from tests.helpers import make_user
        make_user(app, "actor", "Super Admin", all_branches=True)
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
    return u


def _no_tax(app, x):
    db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None
    settings_svc.update({"tax.rate_pct": "0"}, _actor(app), keys=["tax.rate_pct"])


def _sale(app, x, qty="1"):
    order = pos_svc.start_order(_actor(app), {"order_type": "takeaway", "location_id": x["loc_a"]})
    pos_svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": qty})
    db.session.commit()
    totals = pos_svc.compute_totals(order)
    invoice = pos_svc.complete_sale(_actor(app), order, [{"method": "card", "amount": str(totals.total)}])
    db.session.commit()
    return invoice


def test_pl_reconciles_to_a_single_sale(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        _no_tax(app, x)
        today = dt.date.today()
        invoice = _sale(app, x, qty="2")  # 2 x 8.99 = 17.98; cogs = 2 x 150g x 0.008 = 2.40
        pl = svc.profit_and_loss(_actor(app), branch_id=ids["A"], date_from=today, date_to=today)
        assert pl["gross_sales"] == invoice.total == D("17.98")
        assert pl["cogs"] == invoice.cogs_total == D("2.40")
        assert pl["gross_profit"] == D("15.58") and pl["net_profit"] == D("15.58")
        assert pl["order_count"] == 1


def test_pl_subtracts_refunds_and_restocked_cogs(finance):
    app, ids, x, p, f = finance
    from app.models.inventory import Location
    from app.services import inventory as inv_svc
    with app.app_context():
        _no_tax(app, x)
        today = dt.date.today()
        can = InventoryItem(sku="CAN", name="Canned Cola", item_type="finished_product",
                            stock_unit_id=x["piece"], track_batches=True, selling_price="2.00",
                            tax_rate_pct="0")
        db.session.add(can)
        db.session.commit()
        inv_svc.receive_stock(item=can, location=db.session.get(Location, x["loc_a"]), quantity="10",
                              unit_cost="0.80", actor=None)
        db.session.commit()
        order = pos_svc.start_order(_actor(app), {"order_type": "takeaway", "location_id": x["loc_a"]})
        pos_svc.add_line(_actor(app), order, {"item_id": can.id, "quantity": "4"})
        db.session.commit()
        invoice = pos_svc.complete_sale(_actor(app), order, [{"method": "card", "amount": "8.00"}])
        db.session.commit()
        refund_svc.create_refund(_actor(app), invoice, {"reason": "extra",
                                 "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "2",
                                           "restock": True}],
                                 "payments": [{"method": "card", "amount": "4.00"}]})
        db.session.commit()
        pl = svc.profit_and_loss(_actor(app), branch_id=ids["A"], date_from=today, date_to=today)
        assert pl["refunds"] == D("4.00") and pl["net_sales"] == D("4.00")
        assert pl["cogs"] == D("1.60")  # 4 x 0.80 = 3.20, minus 2 restocked x 0.80
        assert pl["gross_profit"] == D("2.40")


def test_pl_subtracts_expenses_for_net_profit(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        _no_tax(app, x)
        today = dt.date.today()
        _sale(app, x, qty="1")  # 8.99, cogs 1.20 -> gross profit 7.79
        expense_svc.create(_actor(app), {"branch_id": str(ids["A"]), "category_id": str(f["category"]),
                                        "amount": "3.00", "method": "card",
                                        "expense_date": today.isoformat()})
        db.session.commit()
        pl = svc.profit_and_loss(_actor(app), branch_id=ids["A"], date_from=today, date_to=today)
        assert pl["gross_profit"] == D("7.79") and pl["expenses"] == D("3.00")
        assert pl["net_profit"] == D("4.79")


def test_pl_date_range_excludes_outside_sales(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        _no_tax(app, x)
        _sale(app, x)
        y = dt.date.today() - dt.timedelta(days=1)
        pl = svc.profit_and_loss(_actor(app), branch_id=ids["A"], date_from=y, date_to=y)
        assert pl["gross_sales"] == D("0.00") and pl["order_count"] == 0


def test_pl_is_branch_scoped(finance):
    app, ids, x, p, f = finance
    from tests.helpers import make_user
    with app.app_context():
        _no_tax(app, x)
        today = dt.date.today()
        _sale(app, x)
        make_user(app, "mgr", "Branch Manager", branches=[ids["B"]])
        from app.models.auth import User
        mgr = db.session.scalar(db.select(User).where(User.username == "mgr"))
        assert svc.profit_and_loss(mgr, date_from=today, date_to=today)["gross_sales"] == D("0.00")


def test_payment_method_breakdown(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        _no_tax(app, x)
        today = dt.date.today()
        _sale(app, x)
        rows = dict(svc.payment_method_breakdown(_actor(app), branch_id=ids["A"],
                                                 date_from=today, date_to=today))
        assert rows["card"] == D("8.99")


def test_daily_closing_includes_wastage(finance):
    app, ids, x, p, f = finance
    from app.models.inventory import Location
    from app.services import inventory as inv_svc
    with app.app_context():
        loc = db.session.get(Location, x["loc_a"])
        beef = db.session.get(InventoryItem, x["beef"])
        inv_svc.receive_stock(item=beef, location=loc, quantity="1000", unit_cost="0.01", actor=None)
        inv_svc.record_wastage(item=beef, location=loc, quantity="100", reason="dropped", actor=None)
        db.session.commit()
        data = svc.daily_closing(_actor(app), branch_id=ids["A"], on_date=dt.date.today())
        # FIFO: the fixture already stocked beef at $0.008/g, so the wasted 100g draws from that
        # older, cheaper batch first, not the $0.01/g batch received here.
        assert data["wastage_cost"] == D("0.80")


def test_sales_by_employee(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        _no_tax(app, x)
        today = dt.date.today()
        _sale(app, x)
        _sale(app, x)
        rows = svc.sales_by_employee(_actor(app), branch_id=ids["A"], date_from=today, date_to=today)
        assert rows[0]["order_count"] == 2 and rows[0]["total"] == D("17.98")
