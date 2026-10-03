"""Web-layer tests for Phase 6: hold/transfer/merge/split, refunds, reservations, customer
credit and loyalty — over real HTTP, including permission and branch scoping."""
from app.extensions import db
from app.models.pos import Customer, Invoice, Order, Reservation, Table
from tests.helpers import login, make_role, make_user, post


def _second_table(app, p):
    from app.models.pos import Floor
    with app.app_context():
        floor = db.session.get(Floor, p["floor"])
        t2 = Table(floor_id=floor.id, branch_id=floor.branch_id, name="T2", capacity=4)
        db.session.add(t2)
        db.session.commit()
        return t2.id


def test_hold_and_resume_through_http(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    assert post(c, f"/pos/orders/{order_id}/hold").status_code == 302
    with app.app_context():
        assert db.session.get(Order, order_id).is_held is True
    assert post(c, f"/pos/orders/{order_id}/resume").status_code == 302
    with app.app_context():
        assert db.session.get(Order, order_id).is_held is False


def test_transfer_merge_split_through_http(pos):
    app, ids, x, p = pos
    t2 = _second_table(app, p)
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/pos/orders/new", {"order_type": "dine_in", "location_id": x["loc_a"], "table_id": p["table"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    with app.app_context():
        from app.models.pos import OrderLine
        line_id = db.session.scalar(db.select(OrderLine.id))

    r = post(c, f"/pos/orders/{order_id}/split", {"line_ids": [str(line_id)]})
    assert r.status_code == 302
    with app.app_context():
        new_order_id = db.session.scalar(db.select(Order.id).where(Order.id != order_id))
        assert len(db.session.get(Order, order_id).lines) == 0

    r2 = post(c, f"/pos/orders/{new_order_id}/transfer-table", {"table_id": t2})
    assert r2.status_code == 302
    with app.app_context():
        assert db.session.get(Order, new_order_id).table_id == t2

    r3 = post(c, f"/pos/orders/{order_id}/merge", {"target_order_id": new_order_id})
    assert r3.status_code == 302
    with app.app_context():
        assert db.session.get(Order, order_id).status == "void"
        assert len(db.session.get(Order, new_order_id).lines) == 1


def test_transfer_and_split_require_pos_transfer_permission(pos):
    app, ids, x, p = pos
    make_role(app, "No Transfer", ["pos.sell", "dashboard.view"])
    make_user(app, "nt", "No Transfer", branches=[ids["A"]])
    c = login(app, "nt")
    post(c, "/pos/orders/new", {"order_type": "dine_in", "location_id": x["loc_a"], "table_id": p["table"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    assert post(c, f"/pos/orders/{order_id}/transfer-table", {"table_id": "999"}).status_code == 403
    assert post(c, f"/pos/orders/{order_id}/split", {"line_ids": []}).status_code == 403


def test_refund_flow_through_http(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    from app.models.auth import User
    from app.services import settings as settings_svc
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).where(User.username == "boss"))
        settings_svc.update({"tax.rate_pct": "0"}, db.session.get(User, uid), keys=["tax.rate_pct"])
        from app.models.inventory import InventoryItem
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None
        db.session.commit()
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    post(c, f"/pos/orders/{order_id}/checkout", {"pay_method": ["card"], "pay_amount": ["8.99"]})
    with app.app_context():
        invoice_id = db.session.scalar(db.select(Invoice.id))
        line_id = db.session.get(Invoice, invoice_id).order.lines[0].id

    receipt_html = c.get(f"/pos/orders/{order_id}/receipt").get_data(as_text=True)
    assert "Refund" in receipt_html

    r = post(c, f"/pos/refunds/invoices/{invoice_id}/new", {
        "reason": "Wrong order", "order_line_id": [str(line_id)], "quantity": ["1"],
        "pay_method": ["card"], "pay_amount": ["8.99"]})
    assert r.status_code == 302
    from app.core.money import D
    with app.app_context():
        assert db.session.get(Invoice, invoice_id).refunded_total == D("8.99")


def test_reservation_flow_through_http(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    import datetime as dt
    tomorrow = (dt.datetime.now() + dt.timedelta(days=1)).replace(hour=19, minute=0, second=0,
                                                                   microsecond=0).isoformat()
    r = post(c, "/pos/reservations/new", {"branch_id": ids["A"], "guest_name": "Priya",
             "party_size": "4", "reserved_at": tomorrow, "table_id": p["table"]})
    assert r.status_code == 302
    with app.app_context():
        res_id = db.session.scalar(db.select(Reservation.id))
    assert post(c, f"/pos/reservations/{res_id}/advance").status_code == 302
    with app.app_context():
        assert db.session.get(Reservation, res_id).status == "confirmed"
    r2 = post(c, f"/pos/reservations/{res_id}/seat", {})
    assert r2.status_code == 302
    with app.app_context():
        res = db.session.get(Reservation, res_id)
        assert res.status == "seated" and res.order_id is not None


def test_customer_credit_and_payment_through_http(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    from app.models.inventory import InventoryItem
    with app.app_context():
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None  # use the 0% global rate
        db.session.commit()
    post(c, "/pos/customers/new", {"name": "Regular Joe"})
    with app.app_context():
        customer_id = db.session.scalar(db.select(Customer.id))
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    post(c, f"/pos/orders/{order_id}/customer", {"customer_id": customer_id})
    r = post(c, f"/pos/orders/{order_id}/checkout", {"pay_method": [], "pay_amount": []})
    assert r.status_code == 302
    from app.core.money import D
    from app.services.customer_ledger import balance_of
    with app.app_context():
        assert balance_of(customer_id) == D("8.99")

    r2 = post(c, f"/pos/customers/{customer_id}/pay", {"amount": "8.99", "method": "cash",
              "branch_id": ids["A"]})
    assert r2.status_code == 302
    with app.app_context():
        assert balance_of(customer_id) == D("0.00")


def test_customer_payment_defaults_to_current_branch_without_a_form_field(pos):
    """Regression: the payment form has no branch selector; an Owner (all-branches access)
    recording a payment must not silently fail validation for lack of one."""
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    from app.models.inventory import InventoryItem
    with app.app_context():
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None
        db.session.commit()
    post(c, "/pos/customers/new", {"name": "Regular Joe"})
    with app.app_context():
        customer_id = db.session.scalar(db.select(Customer.id))
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    post(c, f"/pos/orders/{order_id}/customer", {"customer_id": customer_id})
    post(c, f"/pos/orders/{order_id}/checkout", {"pay_method": [], "pay_amount": []})
    r = post(c, f"/pos/customers/{customer_id}/pay", {"amount": "8.99", "method": "cash"})  # no branch_id
    assert r.status_code == 302
    from app.core.money import D
    from app.services.customer_ledger import balance_of
    with app.app_context():
        assert balance_of(customer_id) == D("0.00")


def test_customer_credit_requires_permission(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    post(login(app, "boss"), "/pos/customers/new", {"name": "X"})
    with app.app_context():
        customer_id = db.session.scalar(db.select(Customer.id))
    make_role(app, "No Credit", ["pos.sell", "customers.view", "dashboard.view"])
    make_user(app, "nc", "No Credit", branches=[ids["A"]])
    c = login(app, "nc")
    assert post(c, f"/pos/customers/{customer_id}/pay", {"amount": "5"}).status_code == 403


def test_pages_render_for_full_access_user(pos):
    app, ids, x, p = pos
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    for url in ("/pos/reservations/", "/pos/reservations/new", "/pos/refunds/"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert "{{" not in r.get_data(as_text=True)
