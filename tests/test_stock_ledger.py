"""
The stock ledger is the heart of Phase 2: every change must be traceable, FIFO must consume
oldest-first, and the cached balance must always equal the sum of movements.
"""
from app.core.errors import BusinessRuleError, ValidationError
from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryItem, Location, StockBatch, StockMovement
from app.services import inventory as svc


def _ctx(app, x):
    return (db.session.get(InventoryItem, x["beef"]), db.session.get(Location, x["loc_a"]),
            db.session.get(Location, x["loc_b"]))


def test_receive_creates_batch_and_movement(inv):
    app, ids, x = inv
    with app.app_context():
        item, loc, _ = _ctx(app, x)
        mv = svc.receive_stock(item=item, location=loc, quantity="10000", unit_cost="0.008",
                               batch_no="B1", actor=None)
        db.session.commit()
        assert mv.previous_balance == 0 and mv.new_balance == D("10000.0000")
        assert svc.current_balance(item.id, loc.id) == D("10000.0000")
        batch = db.session.scalar(db.select(StockBatch).where(StockBatch.batch_no == "B1"))
        assert batch.qty_remaining == D("10000.0000") and batch.unit_cost == D("0.008000")


def test_fifo_consumes_oldest_batch_first_and_splits_across_layers(inv):
    app, ids, x = inv
    with app.app_context():
        item, loc, _ = _ctx(app, x)
        svc.receive_stock(item=item, location=loc, quantity="100", unit_cost="1.00", batch_no="OLD",
                          actor=None)
        svc.receive_stock(item=item, location=loc, quantity="100", unit_cost="2.00", batch_no="NEW",
                          actor=None)
        moves = svc.consume_stock(item=item, location=loc, quantity="150", actor=None)
        db.session.commit()
        assert [str(m.qty) for m in moves] == ["-100.0000", "-50.0000"]
        assert [str(m.unit_cost) for m in moves] == ["1.000000", "2.000000"]  # oldest cost first
        old, new = (db.session.scalar(db.select(StockBatch).where(StockBatch.batch_no == n))
                   for n in ("OLD", "NEW"))
        assert old.qty_remaining == 0 and new.qty_remaining == D("50.0000")
        assert svc.current_balance(item.id, loc.id) == D("50.0000")


def test_cannot_go_negative_by_default(inv):
    app, ids, x = inv
    with app.app_context():
        item, loc, _ = _ctx(app, x)
        svc.receive_stock(item=item, location=loc, quantity="10", unit_cost="1", actor=None)
        try:
            svc.consume_stock(item=item, location=loc, quantity="11", actor=None)
            raise AssertionError("should have raised")
        except BusinessRuleError as e:
            assert "Not enough" in e.message
        db.session.rollback()
        assert svc.current_balance(item.id, loc.id) in (0, D("10.0000"))  # unaffected either way


def test_negative_stock_allowed_when_setting_enabled(inv):
    app, ids, x = inv
    from app.services import settings as settings_svc
    with app.app_context():
        settings_svc.update({"inventory.allow_negative_stock": "on"}, type("A", (), {"id": None})(),
                            keys=["inventory.allow_negative_stock"])
        db.session.commit()
        item, loc, _ = _ctx(app, x)
        moves = svc.consume_stock(item=item, location=loc, quantity="5", actor=None)
        db.session.commit()
        assert svc.current_balance(item.id, loc.id) == D("-5.0000")
        assert moves[0].reason.startswith("stock went negative")


def test_stock_equation_reconciles_after_a_sequence_of_movements(inv):
    """Opening + in - out must always equal the sum of all movement.qty for that item/location."""
    app, ids, x = inv
    with app.app_context():
        item, loc, other = _ctx(app, x)
        svc.receive_stock(item=item, location=loc, quantity="1000", unit_cost="0.01", actor=None)
        svc.consume_stock(item=item, location=loc, quantity="200", actor=None, movement_type="wastage")
        svc.adjust_stock(item=item, location=loc, delta="50", reason="count correction", actor=None,
                         unit_cost="0.02")
        svc.transfer_stock(item=item, from_location=loc, to_location=other, quantity="300", actor=None)
        db.session.commit()
        total = db.session.scalar(
            db.select(db.func.sum(StockMovement.qty)).where(StockMovement.item_id == item.id,
                                                             StockMovement.location_id == loc.id))
        assert total == svc.current_balance(item.id, loc.id) == D("550.0000")
        other_total = db.session.scalar(
            db.select(db.func.sum(StockMovement.qty)).where(StockMovement.item_id == item.id,
                                                             StockMovement.location_id == other.id))
        assert other_total == svc.current_balance(item.id, other.id) == D("300.0000")


def test_transfer_preserves_batch_cost(inv):
    app, ids, x = inv
    with app.app_context():
        item, loc, other = _ctx(app, x)
        svc.receive_stock(item=item, location=loc, quantity="100", unit_cost="3.50", actor=None)
        svc.transfer_stock(item=item, from_location=loc, to_location=other, quantity="40", actor=None)
        db.session.commit()
        dest_batch = db.session.scalar(db.select(StockBatch).where(StockBatch.location_id == other.id))
        assert dest_batch.unit_cost == D("3.500000") and dest_batch.qty_remaining == D("40.0000")


def test_every_movement_is_fully_explained(inv):
    """The spec's core promise: the system can always explain why a balance is what it is."""
    app, ids, x = inv
    with app.app_context():
        item, loc, _ = _ctx(app, x)
        svc.receive_stock(item=item, location=loc, quantity="500", unit_cost="0.02", actor=None,
                          reference_type="opening")
        svc.consume_stock(item=item, location=loc, quantity="120", actor=None,
                          movement_type="wastage", reference_type="wastage", reason="dropped tray")
        db.session.commit()
        moves = list(db.session.scalars(db.select(StockMovement).where(StockMovement.item_id == item.id)
                                        .order_by(StockMovement.id)))
        assert moves[0].previous_balance == 0 and moves[0].new_balance == D("500.0000")
        assert moves[1].previous_balance == D("500.0000") and moves[1].new_balance == D("380.0000")
        assert moves[1].reason == "dropped tray" and moves[1].user_id is None


def test_movements_cannot_be_updated_or_deleted(inv):
    import pytest
    from sqlalchemy.exc import DBAPIError
    app, ids, x = inv
    with app.app_context():
        item, loc, _ = _ctx(app, x)
        mv = svc.receive_stock(item=item, location=loc, quantity="10", unit_cost="1", actor=None)
        db.session.commit()
        with pytest.raises(DBAPIError, match="append-only"):
            db.session.execute(db.text(f"UPDATE stock_movements SET qty = 999 WHERE id = {mv.id}"))
        db.session.rollback()


def test_zero_and_negative_quantity_rejected(inv):
    import pytest
    app, ids, x = inv
    with app.app_context():
        item, loc, _ = _ctx(app, x)
        with pytest.raises(ValidationError):
            svc.receive_stock(item=item, location=loc, quantity="0", unit_cost="1", actor=None)
        with pytest.raises(ValidationError):
            svc.consume_stock(item=item, location=loc, quantity="-5", actor=None)


def test_non_batch_tracked_item_uses_simple_balance_and_last_cost(inv):
    app, ids, x = inv
    with app.app_context():
        item = db.session.get(InventoryItem, x["burger"])  # track_batches=False
        loc = db.session.get(Location, x["loc_a"])
        svc.receive_stock(item=item, location=loc, quantity="20", unit_cost="3.00", actor=None)
        svc.receive_stock(item=item, location=loc, quantity="10", unit_cost="3.50", actor=None)
        moves = svc.consume_stock(item=item, location=loc, quantity="5", actor=None)
        db.session.commit()
        assert len(moves) == 1 and moves[0].unit_cost == D("3.500000")  # most recent cost, no batches
        assert svc.current_balance(item.id, loc.id) == D("25.0000")
        assert db.session.scalar(db.select(db.func.count(StockBatch.id)).where(
            StockBatch.item_id == item.id)) == 0


def test_stock_valuation_sums_remaining_batches(inv):
    app, ids, x = inv
    with app.app_context():
        item, loc, _ = _ctx(app, x)
        svc.receive_stock(item=item, location=loc, quantity="100", unit_cost="2.00", actor=None)
        svc.receive_stock(item=item, location=loc, quantity="50", unit_cost="3.00", actor=None)
        db.session.commit()
        assert svc.stock_valuation(loc.id) == D("350.00")


def test_weighted_average_cost_display(inv):
    app, ids, x = inv
    with app.app_context():
        item, loc, _ = _ctx(app, x)
        svc.receive_stock(item=item, location=loc, quantity="100", unit_cost="2.00", actor=None)
        svc.receive_stock(item=item, location=loc, quantity="100", unit_cost="4.00", actor=None)
        db.session.commit()
        assert svc.current_unit_cost(item, loc) == D("3.000000")
