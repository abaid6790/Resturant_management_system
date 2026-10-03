"""Reservation lifecycle and seating (which starts a real dine-in order)."""
import datetime as dt

from app.core.errors import BusinessRuleError, ValidationError
from app.extensions import db
from app.models.pos import Table
from app.services import reservations as svc


def _actor(app):
    from app.models.auth import User
    u = db.session.scalar(db.select(User).where(User.username == "actor"))
    if u is None:
        from tests.helpers import make_user
        make_user(app, "actor", "Super Admin", all_branches=True)
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
    return u


def _tomorrow_19():
    return (dt.datetime.now() + dt.timedelta(days=1)).replace(hour=19, minute=0, second=0,
                                                               microsecond=0)


def test_create_requires_a_name_or_customer_and_a_time(pos):
    app, ids, x, p = pos
    with app.app_context():
        try:
            svc.create(_actor(app), {"branch_id": str(ids["A"]), "party_size": "2",
                                     "reserved_at": _tomorrow_19().isoformat()})
            raise AssertionError
        except ValidationError as e:
            assert "guest_name" in e.details
        r = svc.create(_actor(app), {"branch_id": str(ids["A"]), "party_size": "4",
                                     "guest_name": "Priya", "guest_phone": "555-0100",
                                     "reserved_at": _tomorrow_19().isoformat(),
                                     "table_id": str(p["table"])})
        db.session.commit()
        assert r.status == "requested" and r.guest_name == "Priya"


def test_lifecycle_confirm_then_seat(pos):
    app, ids, x, p = pos
    with app.app_context():
        r = svc.create(_actor(app), {"branch_id": str(ids["A"]), "party_size": "2",
                                     "guest_name": "Sam", "reserved_at": _tomorrow_19().isoformat(),
                                     "table_id": str(p["table"])})
        db.session.commit()
        svc.advance(_actor(app), r)  # -> confirmed
        db.session.commit()
        assert r.status == "confirmed"
        order = svc.seat(_actor(app), r, location_id=x["loc_a"])
        db.session.commit()
        assert r.status == "seated" and r.order_id == order.id
        assert order.order_type == "dine_in" and order.table_id == p["table"]
        assert db.session.get(Table, p["table"]).status == "occupied"


def test_seat_requires_a_table(pos):
    app, ids, x, p = pos
    with app.app_context():
        r = svc.create(_actor(app), {"branch_id": str(ids["A"]), "party_size": "2",
                                     "guest_name": "Walk-up", "reserved_at": _tomorrow_19().isoformat()})
        db.session.commit()
        try:
            svc.seat(_actor(app), r, location_id=x["loc_a"])
            raise AssertionError
        except ValidationError as e:
            assert "table_id" in e.details


def test_cannot_seat_at_an_occupied_table(pos):
    app, ids, x, p = pos
    with app.app_context():
        from app.services import pos as pos_svc
        pos_svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                          "table_id": p["table"]})
        db.session.commit()
        r = svc.create(_actor(app), {"branch_id": str(ids["A"]), "party_size": "2",
                                     "guest_name": "Late", "reserved_at": _tomorrow_19().isoformat(),
                                     "table_id": str(p["table"])})
        db.session.commit()
        try:
            svc.seat(_actor(app), r, location_id=x["loc_a"])
            raise AssertionError
        except BusinessRuleError as e:
            assert "not available" in e.message


def test_cancel_and_no_show(pos):
    app, ids, x, p = pos
    with app.app_context():
        r1 = svc.create(_actor(app), {"branch_id": str(ids["A"]), "party_size": "2",
                                      "guest_name": "A", "reserved_at": _tomorrow_19().isoformat()})
        r2 = svc.create(_actor(app), {"branch_id": str(ids["A"]), "party_size": "2",
                                      "guest_name": "B", "reserved_at": _tomorrow_19().isoformat()})
        db.session.commit()
        svc.cancel(_actor(app), r1, "plans changed")
        svc.mark_no_show(_actor(app), r2)
        db.session.commit()
        assert r1.status == "cancelled" and r2.status == "no_show"
        try:
            svc.seat(_actor(app), r1, location_id=x["loc_a"])
            raise AssertionError
        except BusinessRuleError:
            pass


def test_existing_customer_fills_in_guest_name(pos):
    app, ids, x, p = pos
    from app.models.pos import Customer
    with app.app_context():
        c = Customer(name="Taylor")
        db.session.add(c)
        db.session.commit()
        r = svc.create(_actor(app), {"branch_id": str(ids["A"]), "party_size": "3",
                                     "customer_id": str(c.id), "reserved_at": _tomorrow_19().isoformat()})
        db.session.commit()
        assert r.customer_id == c.id and r.guest_name == "Taylor"
