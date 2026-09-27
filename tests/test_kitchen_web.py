from app.extensions import db
from app.models.kitchen import KitchenStation, KitchenTicket
from app.models.pos import Order
from tests.helpers import login, make_user, post


def test_station_lifecycle_through_http(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, "/kitchen/stations/new", {"branch_id": ids["A"], "name": "Fry"})
    assert r.status_code == 302
    with app.app_context():
        st = db.session.scalar(db.select(KitchenStation).where(KitchenStation.name == "Fry"))
    assert post(c, f"/kitchen/stations/{st.id}/edit", {"name": "Fryer"}).status_code == 302
    assert post(c, f"/kitchen/stations/{st.id}/status", {"active": "0"}).status_code == 302
    with app.app_context():
        s = db.session.get(KitchenStation, st.id)
        assert s.name == "Fryer" and s.is_active is False


def test_kitchen_staff_can_only_view_and_update_not_manage(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "kstaff", "Kitchen Staff", branches=[ids["A"]])
    c = login(app, "kstaff")
    assert c.get("/kitchen/board").status_code == 200
    assert c.get("/kitchen/stations").status_code == 403
    assert c.get("/kitchen/stations/new").status_code == 403


def test_fire_order_and_advance_through_http(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "cashier", "Cashier", branches=[ids["A"]])
    make_user(app, "kstaff", "Kitchen Staff", branches=[ids["A"]])
    cashier, kstaff = login(app, "cashier"), login(app, "kstaff")
    post(cashier, "/pos/orders/new", {"order_type": "dine_in", "location_id": x["loc_a"],
        "table_id": p["table"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(cashier, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    show_html = cashier.get(f"/pos/orders/{order_id}").get_data(as_text=True)
    assert "Fire to kitchen" in show_html

    r = post(cashier, f"/kitchen/orders/{order_id}/fire", {})
    assert r.status_code == 302
    with app.app_context():
        ticket = db.session.scalar(db.select(KitchenTicket).where(KitchenTicket.order_id == order_id))
        ticket_id = ticket.id
        assert ticket.status == "queued"

    board_html = kstaff.get("/kitchen/board").get_data(as_text=True)
    assert "Classic Burger" in board_html and "Start preparing" in board_html

    assert post(kstaff, f"/kitchen/tickets/{ticket_id}/advance", {"from": "board"}).status_code == 302
    with app.app_context():
        assert db.session.get(KitchenTicket, ticket_id).status == "preparing"
    # a cashier without kitchen.update cannot advance tickets
    assert post(cashier, f"/kitchen/tickets/{ticket_id}/advance", {}).status_code == 403


def test_board_filters_by_station(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    post(c, f"/kitchen/orders/{order_id}/fire", {})
    html = c.get(f"/kitchen/board?station_id={k['station']}").get_data(as_text=True)
    assert "Classic Burger" in html
    with app.app_context():
        pass  # unrelated import just to force a session touch
    other_html = c.get("/kitchen/board?station_id=999999").get_data(as_text=True)
    assert "Classic Burger" not in other_html


def test_fragment_endpoint_returns_partial_not_full_page(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    html = c.get("/kitchen/board?fragment=1").get_data(as_text=True)
    assert "<html" not in html.lower() and "Nothing here" in html


def test_kitchen_routes_are_branch_scoped(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    boss = login(app, "boss")
    post(boss, "/kitchen/stations/new", {"branch_id": ids["B"], "name": "B Station"})
    with app.app_context():
        st = db.session.scalar(db.select(KitchenStation).where(KitchenStation.name == "B Station"))
    mgr = login(app, "mgr")
    assert mgr.get(f"/kitchen/stations/{st.id}/edit").status_code == 404
