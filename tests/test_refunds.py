"""
Refunds: proportional net/tax calculation from the ORIGINAL sale's snapshotted numbers, restock
vs write-off (including the recipe-item restriction), double-refund prevention, and the cash
register / customer-ledger / loyalty side effects.
"""
from app.core.errors import BusinessRuleError, ValidationError
from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryItem
from app.models.pos import Invoice
from app.services import cash as cash_svc
from app.services import inventory as inv_svc
from app.services import pos as pos_svc
from app.services import refunds as svc
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


def _simple_sale(app, x, p, qty="2"):
    """A takeaway sale of `qty` burgers, no tax, fully paid in card (keeps the numbers simple)."""
    settings_svc.update({"tax.rate_pct": "0"}, _actor(app), keys=["tax.rate_pct"])
    db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None
    order = pos_svc.start_order(_actor(app), {"order_type": "takeaway", "location_id": x["loc_a"]})
    pos_svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": qty})
    db.session.commit()
    totals = pos_svc.compute_totals(order)
    invoice = pos_svc.complete_sale(_actor(app), order, [{"method": "card", "amount": str(totals.total)}])
    db.session.commit()
    return invoice


def test_refund_amount_is_proportional_to_quantity(pos):
    app, ids, x, p = pos
    with app.app_context():
        invoice = _simple_sale(app, x, p, qty="2")  # 2 x 8.99 = 17.98
        refund = svc.create_refund(_actor(app), invoice, {
            "reason": "Customer changed mind",
            "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "1", "restock": False}],
            "payments": [{"method": "card", "amount": "8.99"}]})
        db.session.commit()
        assert refund.net_amount == D("8.99") and refund.total_amount == D("8.99")
        assert invoice.order.lines[0].refunded_quantity == D("1.0000")
        assert invoice.refunded_total == D("8.99")


def test_cannot_refund_more_than_outstanding(pos):
    app, ids, x, p = pos
    with app.app_context():
        invoice = _simple_sale(app, x, p, qty="1")
        try:
            svc.create_refund(_actor(app), invoice, {
                "reason": "x", "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "2"}],
                "payments": [{"method": "card", "amount": "17.98"}]})
            raise AssertionError
        except ValidationError as e:
            assert "line0" in e.details


def test_double_refund_prevention_across_two_refunds(pos):
    app, ids, x, p = pos
    with app.app_context():
        invoice = _simple_sale(app, x, p, qty="2")
        line_id = invoice.order.lines[0].id
        svc.create_refund(_actor(app), invoice, {"reason": "r1",
                          "lines": [{"order_line_id": line_id, "quantity": "1"}],
                          "payments": [{"method": "card", "amount": "8.99"}]})
        db.session.commit()
        try:
            svc.create_refund(_actor(app), invoice, {"reason": "r2",
                              "lines": [{"order_line_id": line_id, "quantity": "2"}]})  # only 1 left
            raise AssertionError
        except ValidationError as e:
            assert "line0" in e.details
        svc.create_refund(_actor(app), invoice, {"reason": "r3",
                          "lines": [{"order_line_id": line_id, "quantity": "1"}],
                          "payments": [{"method": "card", "amount": "8.99"}]})
        db.session.commit()
        assert invoice.order.lines[0].refunded_quantity == D("2.0000")


def test_restock_puts_stock_back_at_original_cost(pos):
    app, ids, x, p = pos
    from app.models.inventory import Location
    with app.app_context():
        # a non-recipe item (bottled drink) so restocking is offered
        can = InventoryItem(sku="CAN", name="Canned Cola", item_type="finished_product",
                            stock_unit_id=x["piece"], track_batches=True, selling_price="2.00",
                            tax_rate_pct="0")
        db.session.add(can)
        db.session.commit()
        loc = db.session.get(Location, x["loc_a"])
        inv_svc.receive_stock(item=can, location=loc, quantity="20", unit_cost="0.80", actor=None)
        db.session.commit()
        order = pos_svc.start_order(_actor(app), {"order_type": "takeaway", "location_id": x["loc_a"]})
        pos_svc.add_line(_actor(app), order, {"item_id": can.id, "quantity": "3"})
        db.session.commit()
        invoice = pos_svc.complete_sale(_actor(app), order, [{"method": "card", "amount": "6.00"}])
        db.session.commit()
        assert inv_svc.current_balance(can.id, x["loc_a"]) == D("17.0000")  # 20 - 3

        refund = svc.create_refund(_actor(app), invoice, {"reason": "unopened",
                          "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "2",
                                    "restock": True}],
                          "payments": [{"method": "card", "amount": "4.00"}]})
        db.session.commit()
        assert inv_svc.current_balance(can.id, x["loc_a"]) == D("19.0000")  # 17 + 2 restocked
        assert refund.lines[0].restocked_cost == D("1.60")  # 2 x 0.80


def test_recipe_based_item_cannot_be_restocked(pos):
    app, ids, x, p = pos
    with app.app_context():
        invoice = _simple_sale(app, x, p, qty="1")  # burger has an active recipe
        assert svc.can_restock(invoice.order.lines[0]) is False
        try:
            svc.create_refund(_actor(app), invoice, {"reason": "x",
                              "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "1",
                                        "restock": True}]})
            raise AssertionError
        except ValidationError as e:
            assert "line0" in e.details


def test_refund_payments_must_total_exactly(pos):
    app, ids, x, p = pos
    with app.app_context():
        invoice = _simple_sale(app, x, p, qty="1")
        try:
            svc.create_refund(_actor(app), invoice, {"reason": "x",
                              "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "1"}],
                              "payments": [{"method": "card", "amount": "5.00"}]})  # should be 8.99
            raise AssertionError
        except ValidationError as e:
            assert "payments" in e.details


def test_cash_refund_requires_open_session_and_debits_the_drawer(pos):
    app, ids, x, p = pos
    from app.models.inventory import Location
    with app.app_context():
        invoice = _simple_sale(app, x, p, qty="1")
        try:
            svc.create_refund(_actor(app), invoice, {"reason": "x",
                              "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "1"}],
                              "payments": [{"method": "cash", "amount": "8.99"}]})
            raise AssertionError
        except BusinessRuleError as e:
            assert "cash register" in e.message.lower()
        db.session.rollback()
        invoice = db.session.get(Invoice, invoice.id)
        loc = db.session.get(Location, x["loc_a"])
        session = cash_svc.open_session(_actor(app), loc, "100.00")
        db.session.commit()
        svc.create_refund(_actor(app), invoice, {"reason": "x",
                          "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "1"}],
                          "payments": [{"method": "cash", "amount": "8.99"}]})
        db.session.commit()
        assert cash_svc.session_cash_total(session.id) == D("91.01")  # 100 - 8.99


def test_extras_amount_added_to_total(pos):
    app, ids, x, p = pos
    with app.app_context():
        invoice = _simple_sale(app, x, p, qty="1")
        refund = svc.create_refund(_actor(app), invoice, {"reason": "x",
                          "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "1"}],
                          "extras_amount": "2.50",
                          "payments": [{"method": "card", "amount": "11.49"}]})
        db.session.commit()
        assert refund.extras_amount == D("2.50") and refund.total_amount == D("11.49")


def test_refund_requires_a_reason(pos):
    app, ids, x, p = pos
    with app.app_context():
        invoice = _simple_sale(app, x, p, qty="1")
        try:
            svc.create_refund(_actor(app), invoice, {"reason": "",
                              "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "1"}]})
            raise AssertionError
        except ValidationError as e:
            assert "reason" in e.details
