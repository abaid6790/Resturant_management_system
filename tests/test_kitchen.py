"""KOT firing, station grouping, ticket lifecycle, and cancellation on void."""
from app.core.errors import BusinessRuleError, ValidationError
from app.extensions import db
from app.models.kitchen import KitchenTicket
from app.services import kitchen as svc
from app.services import pos as pos_svc


def _actor(app):
    with app.app_context():
        from app.models.auth import User
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
        if u is None:
            from tests.helpers import make_user
            make_user(app, "actor", "Super Admin", all_branches=True)
            u = db.session.scalar(db.select(User).where(User.username == "actor"))
        return u


def _order_with_lines(app, x, p, items):
    order = pos_svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                              "table_id": p["table"]})
    for item_id, qty in items:
        pos_svc.add_line(_actor(app), order, {"item_id": item_id, "quantity": qty})
    db.session.commit()
    return order


def test_firing_creates_one_ticket_per_station(kitchen):
    app, ids, x, p, k = kitchen
    with app.app_context():
        # burger -> Grill station; bun has no station -> groups under "General" (station_id None)
        order = _order_with_lines(app, x, p, [(x["burger"], "2"), (x["bun"], "1")])
        tickets = svc.fire_order(_actor(app), order)
        db.session.commit()
        assert len(tickets) == 2
        stations = {t.station_id for t in tickets}
        assert stations == {k["station"], None}
        for ln in order.lines:
            assert ln.is_fired is True


def test_firing_again_only_sends_new_lines(kitchen):
    app, ids, x, p, k = kitchen
    with app.app_context():
        order = _order_with_lines(app, x, p, [(x["burger"], "1")])
        svc.fire_order(_actor(app), order)
        db.session.commit()
        try:
            svc.fire_order(_actor(app), order)  # nothing new to send
            raise AssertionError
        except BusinessRuleError as e:
            assert "already" in e.message.lower()
        pos_svc.add_line(_actor(app), order, {"item_id": x["bun"], "quantity": "1"})
        db.session.commit()
        tickets = svc.fire_order(_actor(app), order)  # only the new bun line fires
        db.session.commit()
        assert len(tickets) == 1
        assert len(tickets[0].lines) == 1


def test_ticket_status_only_moves_forward(kitchen):
    app, ids, x, p, k = kitchen
    with app.app_context():
        order = _order_with_lines(app, x, p, [(x["burger"], "1")])
        ticket = svc.fire_order(_actor(app), order)[0]
        db.session.commit()
        assert ticket.status == "queued" and ticket.started_at is None
        svc.advance(_actor(app), ticket)
        db.session.commit()
        assert ticket.status == "preparing" and ticket.started_at is not None
        try:
            svc.advance(_actor(app), ticket, to_status="served")  # can't skip 'ready'
            raise AssertionError
        except BusinessRuleError:
            pass
        svc.advance(_actor(app), ticket)
        assert ticket.status == "ready" and ticket.ready_at is not None
        svc.advance(_actor(app), ticket)
        assert ticket.status == "served" and ticket.served_at is not None
        try:
            svc.advance(_actor(app), ticket)  # already closed
            raise AssertionError
        except BusinessRuleError:
            pass


def test_void_order_cancels_open_tickets_but_not_served_ones(kitchen):
    app, ids, x, p, k = kitchen
    with app.app_context():
        order = _order_with_lines(app, x, p, [(x["burger"], "2")])
        pos_svc.add_line(_actor(app), order, {"item_id": x["bun"], "quantity": "1"})
        db.session.commit()
        tickets = svc.fire_order(_actor(app), order)
        db.session.commit()
        grill_ticket = next(t for t in tickets if t.station_id == k["station"])
        svc.advance(_actor(app), grill_ticket)
        svc.advance(_actor(app), grill_ticket)
        svc.advance(_actor(app), grill_ticket)  # served
        db.session.commit()
        pos_svc.void_order(_actor(app), order, "customer left")
        db.session.commit()
        refreshed = list(db.session.scalars(db.select(KitchenTicket).where(KitchenTicket.order_id == order.id)))
        statuses = {t.station_id: t.status for t in refreshed}
        assert statuses[k["station"]] == "served"  # untouched
        assert statuses[None] == "cancelled"


def test_auto_fire_setting_fires_lines_immediately(kitchen):
    app, ids, x, p, k = kitchen
    from app.services import settings as settings_svc
    with app.app_context():
        settings_svc.update({"kitchen.auto_fire": "on"}, _actor(app), keys=["kitchen.auto_fire"])
        db.session.commit()
        order = pos_svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                                  "table_id": p["table"]})
        line = pos_svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        assert line.is_fired is True
        ticket = db.session.scalar(db.select(KitchenTicket).where(KitchenTicket.order_id == order.id))
        assert ticket is not None and ticket.station_id == k["station"]


def test_station_lifecycle(kitchen):
    app, ids, x, p, k = kitchen
    with app.app_context():
        st = svc.save_station(_actor(app), {"branch_id": str(ids["A"]), "name": "Fry"})
        db.session.commit()
        assert st.branch_id == ids["A"]
        try:
            svc.save_station(_actor(app), {"branch_id": str(ids["A"]), "name": "fry"})  # dup, ci
            raise AssertionError
        except ValidationError:
            pass
        svc.set_station_active(_actor(app), st, False)
        db.session.commit()
        assert st.is_active is False


def test_visible_tickets_defaults_to_open_statuses_and_orders_rush_first(kitchen):
    app, ids, x, p, k = kitchen
    with app.app_context():
        order = _order_with_lines(app, x, p, [(x["bun"], "1")])
        normal = svc.fire_order(_actor(app), order)[0]
        order2 = pos_svc.start_order(_actor(app), {"order_type": "takeaway", "location_id": x["loc_a"]})
        pos_svc.add_line(_actor(app), order2, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        rush = svc.fire_order(_actor(app), order2, priority="rush")[0]
        db.session.commit()
        tickets = svc.visible_tickets(_actor(app))
        assert tickets[0].id == rush.id  # rush sorts first
        served = svc.advance(_actor(app), normal)
        while served.status != "served":
            served = svc.advance(_actor(app), served)
        db.session.commit()
        assert normal.id not in {t.id for t in svc.visible_tickets(_actor(app))}
