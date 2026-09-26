from app.extensions import db
from app.models.pos import CashRegisterSession, Order, Table
from tests.helpers import login, make_role, make_user, post


def test_floor_and_table_lifecycle(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, "/pos/floors/new", {"branch_id": ids["A"], "name": "Rooftop"})
    assert r.status_code == 302
    with app.app_context():
        from app.models.pos import Floor
        floor_id = db.session.scalar(db.select(Floor.id).where(Floor.name == "Rooftop"))
    r = post(c, f"/pos/floors/{floor_id}/tables/new", {"name": "R1", "capacity": "6"})
    assert r.status_code == 302
    with app.app_context():
        t = db.session.scalar(db.select(Table).where(Table.name == "R1"))
        assert t.capacity == 6 and t.status == "available"
    assert post(c, f"/pos/floors/tables/{t.id}/edit", {"name": "R1", "capacity": "8"}).status_code == 302
    with app.app_context():
        assert db.session.get(Table, t.id).capacity == 8


def test_full_order_to_receipt_over_http(pos):
    app, ids, x, p = pos
    uid = make_user(app, "cashier", "Cashier", branches=[ids["A"]])
    c = login(app, "cashier")
    from app.services import settings as settings_svc
    with app.app_context():
        from app.models.auth import User
        from app.models.inventory import InventoryItem
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None  # use the global rate
        settings_svc.update({"pos.cash_requires_open_session": "off", "tax.rate_pct": "0"},
                            db.session.get(User, uid),
                            keys=["pos.cash_requires_open_session", "tax.rate_pct"])
        db.session.commit()

    r = post(c, "/pos/orders/new", {"order_type": "dine_in", "location_id": x["loc_a"],
             "table_id": p["table"]})
    assert r.status_code == 302
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
        assert db.session.get(Table, p["table"]).status == "occupied"

    r = post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "2"})
    assert r.status_code == 302
    html = c.get(f"/pos/orders/{order_id}").get_data(as_text=True)
    assert "Classic Burger" in html and "17.98" in html

    r = post(c, f"/pos/orders/{order_id}/checkout", {"pay_method": ["cash"], "pay_amount": ["17.98"]})
    assert r.status_code == 302
    with app.app_context():
        order = db.session.get(Order, order_id)
        assert order.status == "completed"
        assert db.session.get(Table, p["table"]).status == "available"
        invoice_number = order.invoice.invoice_number
    receipt = c.get(f"/pos/orders/{order_id}/receipt").get_data(as_text=True)
    assert "17.98" in receipt and invoice_number in receipt


def test_cashier_without_pos_sell_is_blocked(pos):
    app, ids, x, p = pos
    make_role(app, "No POS", ["dashboard.view"])
    make_user(app, "nopos", "No POS", branches=[ids["A"]])
    c = login(app, "nopos")
    assert c.get("/pos/orders/new").status_code == 403


def test_price_override_requires_permission(pos):
    app, ids, x, p = pos
    make_role(app, "Cashier No Override", ["pos.sell", "dashboard.view"])
    make_user(app, "co", "Cashier No Override", branches=[ids["A"]])
    c = login(app, "co")
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    r = post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1",
             "unit_price": "1.00"})
    assert r.status_code == 403
    with app.app_context():
        assert len(db.session.get(Order, order_id).lines) == 0  # nothing was added


def test_cash_register_open_close_flow(pos):
    app, ids, x, p = pos
    make_user(app, "cashier", "Cashier", branches=[ids["A"]])
    c = login(app, "cashier")
    r = post(c, "/pos/cash/open", {"location_id": x["loc_a"], "opening_float": "50.00"})
    assert r.status_code == 302
    with app.app_context():
        sess = db.session.scalar(db.select(CashRegisterSession))
    assert c.get(f"/pos/cash/{sess.id}").status_code == 200
    r2 = post(c, "/pos/cash/open", {"location_id": x["loc_a"], "opening_float": "10.00"})
    assert r2.status_code == 422  # already open
    r3 = post(c, f"/pos/cash/{sess.id}/close", {"counted_cash": "50.00"})
    assert r3.status_code == 302
    with app.app_context():
        s = db.session.get(CashRegisterSession, sess.id)
        assert s.status == "closed" and s.variance == 0


def test_orders_are_branch_scoped(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    boss = login(app, "boss")
    post(boss, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_b"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    mgr = login(app, "mgr")
    assert mgr.get(f"/pos/orders/{order_id}").status_code == 404
