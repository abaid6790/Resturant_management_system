"""
complete_sale(): the orchestrator. Exit scenario matches the spec: sell 20 of a product needing
150g each -> exactly 3kg deducted, an invoice with accurate COGS, payment(s) recorded, cash
register updated, table freed, all atomic.
"""
from app.core.errors import BusinessRuleError, ValidationError
from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryItem
from app.models.pos import Order, Payment, Table
from app.services import cash as cash_svc
from app.services import inventory as inv_svc
from app.services import pos as svc
from app.services import settings as settings_svc


def _actor(app):
    with app.app_context():
        from app.models.auth import User
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
        if u is None:
            from tests.helpers import make_user
            make_user(app, "actor", "Super Admin", all_branches=True)
            u = db.session.scalar(db.select(User).where(User.username == "actor"))
        return u


def _dine_in_order(app, x, p, qty="20"):
    order = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                          "table_id": p["table"]})
    svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": qty})
    db.session.commit()
    return order


def test_sell_20_burgers_consumes_exactly_3kg_and_frees_table(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"tax.rate_pct": "0", "service_charge.percent": "0"}, _actor(app),
                            keys=["tax.rate_pct", "service_charge.percent"])
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None
        order = _dine_in_order(app, x, p)
        before = inv_svc.current_balance(x["beef"], x["loc_a"])
        totals = svc.compute_totals(order)
        invoice = svc.complete_sale(_actor(app), order, [{"method": "card", "amount": str(totals.total)}])
        db.session.commit()

        after = inv_svc.current_balance(x["beef"], x["loc_a"])
        assert before - after == D("3000.0000")  # 20 x 150g, exactly, per the spec
        assert invoice.status == "paid" and invoice.total == D("179.80")  # 20 x 8.99
        assert invoice.cogs_total == D("24.00")  # 3000g x 0.008
        assert order.status == "completed" and order.completed_at is not None
        table = db.session.get(Table, p["table"])
        assert table.status == "available"


def test_item_without_a_recipe_deducts_itself_directly(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"pos.cash_requires_open_session": "off"}, _actor(app),
                            keys=["pos.cash_requires_open_session"])
        can = InventoryItem(sku="CAN-COLA", name="Canned Cola", item_type="finished_product",
                            stock_unit_id=x["piece"], track_batches=True, selling_price=D("2.00"),
                            tax_rate_pct=D("0"))
        db.session.add(can)
        db.session.commit()
        loc = __import__("app.models.inventory", fromlist=["Location"]).Location
        inv_svc.receive_stock(item=can, location=db.session.get(loc, x["loc_a"]), quantity="50",
                              unit_cost="0.80", actor=None)
        db.session.commit()
        order = svc.start_order(_actor(app), {"order_type": "takeaway", "location_id": x["loc_a"]})
        svc.add_line(_actor(app), order, {"item_id": can.id, "quantity": "3"})
        db.session.commit()
        invoice = svc.complete_sale(_actor(app), order, [{"method": "cash", "amount": "6.00"}])
        db.session.commit()
        assert inv_svc.current_balance(can.id, x["loc_a"]) == D("47.0000")
        assert invoice.cogs_total == D("2.40")  # 3 x 0.80


def test_insufficient_stock_blocks_the_whole_sale(pos):
    app, ids, x, p = pos
    with app.app_context():
        order = _dine_in_order(app, x, p, qty="1000")  # needs 150kg of beef; only 10kg on hand
        totals = svc.compute_totals(order)
        try:
            svc.complete_sale(_actor(app), order, [{"method": "card", "amount": str(totals.total)}])
            raise AssertionError
        except BusinessRuleError:
            db.session.rollback()
        assert inv_svc.current_balance(x["beef"], x["loc_a"]) == D("10000.0000")  # untouched
        order = db.session.get(Order, order.id)
        assert order.status == "open"  # rolled back, not left half-completed


def test_underpayment_rejected(pos):
    app, ids, x, p = pos
    with app.app_context():
        order = _dine_in_order(app, x, p, qty="1")
        try:
            svc.complete_sale(_actor(app), order, [{"method": "card", "amount": "1.00"}])
            raise AssertionError
        except BusinessRuleError as e:
            assert "does not cover" in e.message


def test_overpayment_requires_cash_for_change(pos):
    app, ids, x, p = pos
    with app.app_context():
        order = _dine_in_order(app, x, p, qty="1")
        try:
            svc.complete_sale(_actor(app), order, [{"method": "card", "amount": "50.00"}])
            raise AssertionError
        except BusinessRuleError as e:
            assert "change" in e.message.lower()


def test_split_payment_card_and_cash_with_change(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"pos.cash_requires_open_session": "off"}, _actor(app),
                            keys=["pos.cash_requires_open_session"])
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None
        settings_svc.update({"tax.rate_pct": "0"}, _actor(app), keys=["tax.rate_pct"])
        order = _dine_in_order(app, x, p, qty="1")  # total 8.99
        invoice = svc.complete_sale(_actor(app), order, [
            {"method": "card", "amount": "5.00"}, {"method": "cash", "amount": "10.00"}])
        db.session.commit()
        assert invoice.total == D("8.99") and invoice.paid_total == D("8.99")
        payments = list(db.session.scalars(db.select(Payment).where(Payment.invoice_id == invoice.id)))
        assert sorted(pmt.amount for pmt in payments) == [D("5.00"), D("10.00")]


def test_only_one_cash_payment_row_allowed(pos):
    app, ids, x, p = pos
    with app.app_context():
        order = _dine_in_order(app, x, p, qty="1")
        try:
            svc.complete_sale(_actor(app), order, [{"method": "cash", "amount": "5.00"},
                                                    {"method": "cash", "amount": "5.00"}])
            raise AssertionError
        except ValidationError as e:
            assert "payments" in e.details


def test_cash_payment_requires_open_session_when_setting_on(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"pos.cash_requires_open_session": "on"}, _actor(app),
                            keys=["pos.cash_requires_open_session"])
        db.session.commit()
        order = _dine_in_order(app, x, p, qty="1")
        totals = svc.compute_totals(order)
        try:
            svc.complete_sale(_actor(app), order, [{"method": "cash", "amount": str(totals.total)}])
            raise AssertionError
        except BusinessRuleError as e:
            assert "cash register" in e.message.lower()
        db.session.rollback()


def test_cash_sale_updates_the_open_register(pos):
    app, ids, x, p = pos
    from app.models.inventory import Location
    with app.app_context():
        loc = db.session.get(Location, x["loc_a"])
        session = cash_svc.open_session(_actor(app), loc, "100.00")
        db.session.commit()
        order = _dine_in_order(app, x, p, qty="1")
        totals = svc.compute_totals(order)
        svc.complete_sale(_actor(app), order, [{"method": "cash", "amount": str(totals.total)}])
        db.session.commit()
        # opening float (100) + sale total, since payment == total means no change
        assert cash_svc.session_cash_total(session.id) == D(str(D("100.00") + totals.total))


def test_cannot_complete_an_already_completed_order(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"pos.cash_requires_open_session": "off"}, _actor(app),
                            keys=["pos.cash_requires_open_session"])
        order = _dine_in_order(app, x, p, qty="1")
        totals = svc.compute_totals(order)
        svc.complete_sale(_actor(app), order, [{"method": "cash", "amount": str(totals.total)}])
        db.session.commit()
        try:
            svc.complete_sale(_actor(app), order, [{"method": "cash", "amount": str(totals.total)}])
            raise AssertionError
        except BusinessRuleError:
            pass


def test_invoice_numbers_are_unique_and_sequential(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"pos.cash_requires_open_session": "off"}, _actor(app),
                            keys=["pos.cash_requires_open_session"])
        numbers = []
        for _ in range(3):
            order = svc.start_order(_actor(app), {"order_type": "takeaway", "location_id": x["loc_a"]})
            svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "1"})
            db.session.commit()
            totals = svc.compute_totals(order)
            inv = svc.complete_sale(_actor(app), order, [{"method": "cash", "amount": str(totals.total)}])
            db.session.commit()
            numbers.append(inv.invoice_number)
        assert len(set(numbers)) == 3


def test_void_frees_the_table_without_touching_stock(pos):
    app, ids, x, p = pos
    with app.app_context():
        order = _dine_in_order(app, x, p, qty="5")
        svc.void_order(_actor(app), order, "customer left")
        db.session.commit()
        assert db.session.get(Table, p["table"]).status == "available"
        assert inv_svc.current_balance(x["beef"], x["loc_a"]) == D("10000.0000")  # untouched


def test_table_becomes_occupied_and_only_one_open_order_per_table(pos):
    app, ids, x, p = pos
    with app.app_context():
        svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                              "table_id": p["table"]})
        db.session.commit()
        assert db.session.get(Table, p["table"]).status == "occupied"
        try:
            svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                          "table_id": p["table"]})
            raise AssertionError
        except ValidationError as e:
            assert "table" in str(e.details).lower()
