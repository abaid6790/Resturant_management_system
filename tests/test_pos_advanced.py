"""Hold/resume and table transfer/merge/split."""
from app.core.errors import BusinessRuleError
from app.extensions import db
from app.models.pos import Table
from app.services import pos as svc


def _actor(app):
    with app.app_context():
        from app.models.auth import User
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
        if u is None:
            from tests.helpers import make_user
            make_user(app, "actor", "Super Admin", all_branches=True)
            u = db.session.scalar(db.select(User).where(User.username == "actor"))
        return u


def _second_table(app, x, p):
    with app.app_context():
        from app.models.pos import Floor
        floor = db.session.get(Floor, p["floor"])
        t2 = Table(floor_id=floor.id, branch_id=floor.branch_id, name="T2", capacity=4)
        db.session.add(t2)
        db.session.commit()
        return t2.id


def test_hold_and_resume(pos):
    app, ids, x, p = pos
    with app.app_context():
        order = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                              "table_id": p["table"]})
        db.session.commit()
        svc.hold_order(_actor(app), order)
        db.session.commit()
        assert order.is_held is True
        svc.resume_order(_actor(app), order)
        db.session.commit()
        assert order.is_held is False


def test_transfer_table_frees_old_and_occupies_new(pos):
    app, ids, x, p = pos
    t2 = _second_table(app, x, p)
    with app.app_context():
        order = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                              "table_id": p["table"]})
        db.session.commit()
        svc.transfer_table(_actor(app), order, t2)
        db.session.commit()
        assert order.table_id == t2
        assert db.session.get(Table, p["table"]).status == "available"
        assert db.session.get(Table, t2).status == "occupied"


def test_cannot_transfer_to_occupied_table(pos):
    app, ids, x, p = pos
    t2 = _second_table(app, x, p)
    with app.app_context():
        order1 = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                               "table_id": p["table"]})
        svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                               "table_id": t2})
        db.session.commit()
        try:
            svc.transfer_table(_actor(app), order1, t2)
            raise AssertionError
        except BusinessRuleError as e:
            assert "not available" in e.message


def test_merge_orders_moves_lines_and_closes_source(pos):
    app, ids, x, p = pos
    t2 = _second_table(app, x, p)
    with app.app_context():
        source = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                               "table_id": p["table"]})
        svc.add_line(_actor(app), source, {"item_id": x["burger"], "quantity": "1"})
        target = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                               "table_id": t2})
        svc.add_line(_actor(app), target, {"item_id": x["burger"], "quantity": "2"})
        db.session.commit()
        svc.merge_orders(_actor(app), source, target)
        db.session.commit()
        assert len(target.lines) == 2
        assert source.status == "void" and "Merged into" in source.void_reason
        assert db.session.get(Table, p["table"]).status == "available"
        assert db.session.get(Table, t2).status == "occupied"  # target's table untouched


def test_cannot_merge_once_fired(kitchen):
    app, ids, x, p, k = kitchen
    t2 = _second_table(app, x, p)
    with app.app_context():
        source = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                               "table_id": p["table"]})
        svc.add_line(_actor(app), source, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        from app.services import kitchen as kitchen_svc
        kitchen_svc.fire_order(_actor(app), source)
        target = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                               "table_id": t2})
        db.session.commit()
        try:
            svc.merge_orders(_actor(app), source, target)
            raise AssertionError
        except BusinessRuleError as e:
            assert "kitchen" in e.message.lower()


def test_split_order_creates_new_order_with_selected_lines(pos):
    app, ids, x, p = pos
    t2 = _second_table(app, x, p)
    with app.app_context():
        source = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                               "table_id": p["table"]})
        l1 = svc.add_line(_actor(app), source, {"item_id": x["burger"], "quantity": "1"})
        l2 = svc.add_line(_actor(app), source, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        new_order = svc.split_order(_actor(app), source, [l2.id], t2)
        db.session.commit()
        assert len(source.lines) == 1 and source.lines[0].id == l1.id
        assert len(new_order.lines) == 1 and new_order.lines[0].id == l2.id
        assert new_order.table_id == t2 and new_order.split_from_order_id == source.id
        assert db.session.get(Table, t2).status == "occupied"


def test_split_without_new_table_keeps_same_table_no_table(pos):
    app, ids, x, p = pos
    with app.app_context():
        source = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                               "table_id": p["table"]})
        l1 = svc.add_line(_actor(app), source, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        new_order = svc.split_order(_actor(app), source, [l1.id])
        db.session.commit()
        assert new_order.table_id is None


def test_cannot_split_fired_lines(kitchen):
    app, ids, x, p, k = kitchen
    with app.app_context():
        source = svc.start_order(_actor(app), {"order_type": "dine_in", "location_id": x["loc_a"],
                                               "table_id": p["table"]})
        line = svc.add_line(_actor(app), source, {"item_id": x["burger"], "quantity": "1"})
        db.session.commit()
        from app.services import kitchen as kitchen_svc
        kitchen_svc.fire_order(_actor(app), source)
        db.session.commit()
        try:
            svc.split_order(_actor(app), source, [line.id])
            raise AssertionError
        except BusinessRuleError as e:
            assert "kitchen" in e.message.lower()
