from app.extensions import db
from app.models.pos import CashRegisterSession, Customer, Floor, Order, Table
from tests.helpers import login, make_role, make_user, post


def _actor(app):
    with app.app_context():
        from app.models.auth import User
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
        if u is None:
            from tests.helpers import make_user as _mk
            _mk(app, "actor", "Super Admin", all_branches=True)
            u = db.session.scalar(db.select(User).where(User.username == "actor"))
        return u


def test_customer_crud_through_http(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, "/pos/customers/new", {"name": "Jane Doe", "phone": "555-1234"})
    assert r.status_code == 302
    with app.app_context():
        cust = db.session.scalar(db.select(Customer).where(Customer.name == "Jane Doe"))
    assert post(c, f"/pos/customers/{cust.id}/edit", {"name": "Jane D.", "phone": "555-9999"}).status_code == 302
    assert post(c, f"/pos/customers/{cust.id}/status", {"active": "0"}).status_code == 302
    with app.app_context():
        cu = db.session.get(Customer, cust.id)
        assert cu.name == "Jane D." and cu.phone == "555-9999" and cu.is_active is False
    assert c.get("/pos/customers/?q=Jane").status_code == 200


def test_customer_requires_name_and_valid_email(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    assert post(c, "/pos/customers/new", {"name": ""}).status_code == 422
    assert post(c, "/pos/customers/new", {"name": "Bob", "email": "not-an-email"}).status_code == 422


def test_floor_deactivate_and_duplicate_name_rejected(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, "/pos/floors/new", {"branch_id": ids["A"], "name": "Ground Floor"})
    assert r.status_code == 422  # duplicate of the fixture's floor
    with app.app_context():
        floor_id = db.session.scalar(db.select(Floor.id))
    assert post(c, f"/pos/floors/{floor_id}/status", {"active": "0"}).status_code == 302
    with app.app_context():
        assert db.session.get(Floor, floor_id).is_active is False


def test_table_with_open_order_cannot_be_deactivated(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    with app.app_context():
        from app.services import pos as pos_svc
        pos_svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                          "table_id": p["table"]})
        db.session.commit()
    r = post(c, f"/pos/floors/tables/{p['table']}/status", {"active": "0"})
    assert r.status_code == 302  # redirects with a flashed error, doesn't crash
    with app.app_context():
        assert db.session.get(Table, p["table"]).is_active is True  # unchanged


def test_duplicate_table_name_on_same_floor_rejected(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    with app.app_context():
        floor_id = db.session.scalar(db.select(Floor.id))
    r = post(c, f"/pos/floors/{floor_id}/tables/new", {"name": "T1", "capacity": "2"})
    assert r.status_code == 422


def test_cash_in_and_out_through_http(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/pos/cash/open", {"location_id": x["loc_a"], "opening_float": "100.00"})
    with app.app_context():
        sess_id = db.session.scalar(db.select(CashRegisterSession.id))
    assert post(c, f"/pos/cash/{sess_id}/cash-in", {"amount": "20.00", "reason": "float top-up"}).status_code == 302
    assert post(c, f"/pos/cash/{sess_id}/cash-out", {"amount": "5.00", "reason": "petty cash"}).status_code == 302
    from app.services.cash import session_cash_total
    with app.app_context():
        assert session_cash_total(sess_id) == 115  # 100 + 20 - 5
    assert post(c, f"/pos/cash/{sess_id}/cash-in", {"amount": "0", "reason": "bad"}).status_code == 302


def test_cash_in_out_require_permission_beyond_session(pos):
    app, ids, x, p = pos
    make_role(app, "Cashier No Manage", ["cash.session", "dashboard.view"])
    make_user(app, "c2", "Cashier No Manage", branches=[ids["A"]])
    c = login(app, "c2")
    post(c, "/pos/cash/open", {"location_id": x["loc_a"], "opening_float": "50.00"})
    with app.app_context():
        sess_id = db.session.scalar(db.select(CashRegisterSession.id))
    assert post(c, f"/pos/cash/{sess_id}/cash-in", {"amount": "10", "reason": "x"}).status_code == 403


def test_order_list_filters_and_void_flow(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)  # orders.view_all, unlike plain Cashier
    c = login(app, "boss")
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    assert "ORD-" in c.get("/pos/orders/?status=open").get_data(as_text=True)
    r = post(c, f"/pos/orders/{order_id}/void", {"reason": "customer cancelled"})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.get(Order, order_id).status == "void"
    # voiding again is a no-op error, not a crash
    assert post(c, f"/pos/orders/{order_id}/void", {"reason": "again"}).status_code == 302


def test_remove_line_and_set_customer(pos):
    app, ids, x, p = pos
    make_user(app, "cashier", "Cashier", branches=[ids["A"]])
    c = login(app, "cashier")
    post(c, "/pos/customers/new", {"name": "Sam"})
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
        customer_id = db.session.scalar(db.select(Customer.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    with app.app_context():
        from app.models.pos import OrderLine
        line_id = db.session.scalar(db.select(OrderLine.id))
    assert post(c, f"/pos/orders/{order_id}/lines/{line_id}/remove").status_code == 302
    with app.app_context():
        assert len(db.session.get(Order, order_id).lines) == 0
    assert post(c, f"/pos/orders/{order_id}/customer", {"customer_id": customer_id}).status_code == 302
    with app.app_context():
        assert db.session.get(Order, order_id).customer_id == customer_id


def test_checkout_shows_error_inline_on_insufficient_payment(pos):
    app, ids, x, p = pos
    make_user(app, "cashier", "Cashier", branches=[ids["A"]])
    c = login(app, "cashier")
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    r = post(c, f"/pos/orders/{order_id}/checkout", {"pay_method": ["cash"], "pay_amount": ["1.00"]})
    assert r.status_code == 422
    with app.app_context():
        assert db.session.get(Order, order_id).status == "open"
