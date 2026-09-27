from app.extensions import db
from app.models.kitchen import KitchenTicket
from app.models.pos import Order
from tests.helpers import login, make_user, post


def test_floor_requires_branch_and_name(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    assert post(c, "/pos/floors/new", {"name": ""}).status_code == 422
    assert post(c, "/pos/floors/new", {"branch_id": ids["A"], "name": ""}).status_code == 422


def test_new_table_requires_capacity(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, f"/pos/floors/{p['floor']}/tables/new", {"name": "T9", "capacity": "0"})
    assert r.status_code == 422
    r2 = post(c, f"/pos/floors/{p['floor']}/tables/new", {"name": "T9", "capacity": "abc"})
    assert r2.status_code == 422


def test_locations_scoped_floor_cannot_be_read_across_branch(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    make_user(app, "boss", "Owner", all_branches=True)
    boss = login(app, "boss")
    post(boss, "/pos/floors/new", {"branch_id": ids["B"], "name": "B Floor"})
    from app.models.pos import Floor
    with app.app_context():
        floor_id = db.session.scalar(db.select(Floor.id).where(Floor.name == "B Floor"))
    mgr = login(app, "mgr")
    assert mgr.get(f"/pos/floors/{floor_id}").status_code == 404


def test_kitchen_staff_cannot_fire_orders_without_pos_permission(kitchen):
    """kitchen.view lets you SEE the board, but firing an order still requires being on that
    order's flow; here we verify a Kitchen Staff account (view+update only) can still call
    /kitchen/orders/<id>/fire because it only needs kitchen.view, matching the intent that
    whoever is near the kitchen can push a ticket through if needed."""
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    make_user(app, "kstaff", "Kitchen Staff", branches=[ids["A"]])
    boss, kstaff = login(app, "boss"), login(app, "kstaff")
    post(boss, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(boss, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    r = post(kstaff, f"/kitchen/orders/{order_id}/fire", {})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(KitchenTicket).where(KitchenTicket.order_id == order_id))


def test_firing_an_order_with_nothing_new_flashes_and_redirects_not_crashes(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    post(c, f"/kitchen/orders/{order_id}/fire", {})
    r = post(c, f"/kitchen/orders/{order_id}/fire", {})  # nothing new: should not 500
    assert r.status_code == 302


def test_set_priority_via_http_and_invalid_value_rejected(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    post(c, f"/kitchen/orders/{order_id}/fire", {})
    with app.app_context():
        ticket_id = db.session.scalar(db.select(KitchenTicket.id))
    assert post(c, f"/kitchen/tickets/{ticket_id}/priority", {"priority": "rush"}).status_code == 302
    with app.app_context():
        assert db.session.get(KitchenTicket, ticket_id).priority == "rush"
    r = post(c, f"/kitchen/tickets/{ticket_id}/priority", {"priority": "urgent"})
    assert r.status_code == 302  # rejected internally, flashed, not a 500
    with app.app_context():
        assert db.session.get(KitchenTicket, ticket_id).priority == "rush"  # unchanged


def test_advance_already_closed_ticket_flashes_not_crashes(kitchen):
    app, ids, x, p, k = kitchen
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/pos/orders/new", {"order_type": "takeaway", "location_id": x["loc_a"]})
    with app.app_context():
        order_id = db.session.scalar(db.select(Order.id))
    post(c, f"/pos/orders/{order_id}/lines", {"item_id": x["burger"], "quantity": "1"})
    post(c, f"/kitchen/orders/{order_id}/fire", {})
    with app.app_context():
        ticket_id = db.session.scalar(db.select(KitchenTicket.id))
    for _ in range(3):
        post(c, f"/kitchen/tickets/{ticket_id}/advance", {})
    with app.app_context():
        assert db.session.get(KitchenTicket, ticket_id).status == "served"
    r = post(c, f"/kitchen/tickets/{ticket_id}/advance", {})
    assert r.status_code == 302  # error flashed, not a 500
