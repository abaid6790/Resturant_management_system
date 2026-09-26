"""
Order pricing: line/order discounts, tax (tax-exclusive, per-item override), service charge,
delivery charge. The order-level discount must be spread across lines (money.allocate) so tax
stays correct per line even when lines have different tax rates.
"""
from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryItem
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


def _order(app, x, p, order_type="dine_in", table=True):
    return svc.start_order(_actor(app), {
        "order_type": order_type, "location_id": x["loc_a"],
        "table_id": p["table"] if (order_type == "dine_in" and table) else None})


def test_simple_line_totals_no_tax_no_discount(pos):
    app, ids, x, p = pos
    with app.app_context():
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None  # use the global rate
        settings_svc.update({"tax.rate_pct": "0"}, _actor(app), keys=["tax.rate_pct"])
        order = _order(app, x, p)
        svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "2"})
        db.session.commit()
        totals = svc.compute_totals(order)
        assert totals.subtotal == D("17.98") and totals.tax_total == D("0.00")
        assert totals.total == D("17.98")


def test_tax_is_exclusive_and_added_on_top(pos):
    app, ids, x, p = pos
    with app.app_context():
        order = _order(app, x, p)  # burger has tax_rate_pct=16 set in fixture
        svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        totals = svc.compute_totals(order)
        assert totals.subtotal == D("8.99")
        assert totals.tax_total == D("1.44")  # 16% of 8.99, rounded
        assert totals.total == D("10.43")


def test_item_tax_rate_overrides_global_default(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"tax.rate_pct": "5"}, _actor(app), keys=["tax.rate_pct"])
        order = _order(app, x, p)  # burger's own 16% should win over the global 5%
        svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        assert svc.compute_totals(order).tax_total == D("1.44")


def test_line_percentage_discount(pos):
    app, ids, x, p = pos
    with app.app_context():
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None
        settings_svc.update({"tax.rate_pct": "0"}, _actor(app), keys=["tax.rate_pct"])
        order = _order(app, x, p)
        line = svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "2"})
        line.discount_kind, line.discount_value = "percentage", D("10")
        db.session.commit()
        totals = svc.compute_totals(order)
        assert totals.discount_total == D("1.80")  # 10% of 17.98
        assert totals.total == D("16.18")


def test_order_level_discount_spread_across_lines_for_tax(pos):
    """Two lines with different tax rates: order discount must apply proportionally before tax."""
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"tax.rate_pct": "0"}, _actor(app), keys=["tax.rate_pct"])
        bun = db.session.get(InventoryItem, x["bun"])
        bun.selling_price, bun.tax_rate_pct = D("1.00"), D("10")
        db.session.commit()
        order = _order(app, x, p)
        svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "1"})  # 8.99 @ 16%
        svc.add_line(_actor(app), order, {"item_id": x["bun"], "quantity": "1"})  # 1.00 @ 10%
        svc.set_discount(_actor(app), order, "fixed", D("1.00"), "test")
        db.session.commit()
        totals = svc.compute_totals(order)
        # discount split proportional to (8.99, 1.00) -> mostly off the burger line
        assert totals.discount_total == D("1.00")
        assert totals.subtotal == D("9.99")
        # net = 8.99 - 0.90 = 8.09 @16% = 1.29 ; 1.00 - 0.10 = 0.90 @10% = 0.09 -> total tax 1.38
        assert totals.tax_total == D("1.38")
        assert totals.total == D(str(totals.subtotal - totals.discount_total + totals.tax_total))


def test_fixed_discount_never_exceeds_subtotal(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"tax.rate_pct": "0"}, _actor(app), keys=["tax.rate_pct"])
        order = _order(app, x, p)
        svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "1"})
        svc.set_discount(_actor(app), order, "fixed", D("100.00"), "generous")
        db.session.commit()
        totals = svc.compute_totals(order)
        assert totals.discount_total == D("8.99") and totals.total == D("0.00")


def test_service_charge_applies_to_net_of_discount_and_dine_in_only(pos):
    app, ids, x, p = pos
    with app.app_context():
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None
        settings_svc.update({"tax.rate_pct": "0", "service_charge.percent": "10",
                             "service_charge.dine_in_only": "on"}, _actor(app),
                            keys=["tax.rate_pct", "service_charge.percent", "service_charge.dine_in_only"])
        dine_in = _order(app, x, p, "dine_in")
        svc.add_line(_actor(app), dine_in, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        t = svc.compute_totals(dine_in)
        assert t.service_charge_total == D("0.90")  # 10% of 8.99
        assert t.total == D("9.89")

        takeaway = _order(app, x, p, "takeaway")
        svc.add_line(_actor(app), takeaway, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        assert svc.compute_totals(takeaway).service_charge_total == D("0.00")


def test_service_charge_taxable_setting(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"tax.rate_pct": "10", "service_charge.percent": "10",
                             "service_charge.taxable": "on", "service_charge.dine_in_only": "off"},
                            _actor(app), keys=["tax.rate_pct", "service_charge.percent",
                                              "service_charge.taxable", "service_charge.dine_in_only"])
        item = db.session.get(InventoryItem, x["burger"])
        item.tax_rate_pct = None  # use the global 10% for this test
        order = _order(app, x, p, "takeaway", table=False)
        svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        t = svc.compute_totals(order)
        # 8.99 @10% tax = 0.90 ; service charge 10% of 8.99 = 0.90 ; tax on service charge = 0.09
        assert t.tax_total == D("0.99") and t.service_charge_total == D("0.90")


def test_delivery_charge_only_on_delivery_orders(pos):
    app, ids, x, p = pos
    with app.app_context():
        order = svc.start_order(_actor(app), {"order_type": "delivery", "location_id": x["loc_a"]})
        svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "1"})
        svc.set_delivery_charge(_actor(app), order, "3.50")
        db.session.commit()
        assert svc.compute_totals(order).delivery_charge_total == D("3.50")


def test_discount_over_limit_needs_large_discount_permission(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"pos.discount_limit_pct": "20"}, _actor(app),
                            keys=["pos.discount_limit_pct"])
        db.session.commit()
    from tests.helpers import make_role, make_user
    make_role(app, "Cashier No Large", ["pos.sell", "pos.discount", "dashboard.view"])
    small_actor_id = make_user(app, "small", "Cashier No Large", branches=[ids["A"]])
    with app.app_context():
        from app.models.auth import User
        actor = db.session.get(User, small_actor_id)
        order = _order(app, x, p)
        svc.add_line(actor, order, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        try:
            svc.set_discount(actor, order, "percentage", D("50"), "too big")
            raise AssertionError
        except Exception as e:  # noqa: BLE001
            from app.core.errors import PermissionDeniedError
            assert isinstance(e, PermissionDeniedError)
        svc.set_discount(actor, order, "percentage", D("10"), "fine")  # under the limit: OK
        db.session.commit()
        assert order.discount_value == D("10.00")
