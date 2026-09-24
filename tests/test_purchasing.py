"""
Purchase order lifecycle, receiving into the stock ledger, invoices/payments against the supplier
ledger, and purchase returns. Exit scenario matches the spec: buying stock creates a batch, a
movement, a cost, and a supplier-ledger entry, all atomically.
"""
from app.core.errors import BusinessRuleError, ValidationError
from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryItem, StockBatch
from app.models.purchasing import PurchaseInvoice, Supplier
from app.services import inventory as inv_svc
from app.services import purchasing as svc
from app.services import settings as settings_svc


def _create_po(app, x, supplier_id, qty="1000", cost="0.008"):
    return svc.create_po(_actor(app), {
        "supplier_id": supplier_id, "location_id": x["loc_a"],
        "lines": [{"item_id": x["beef"], "quantity": qty, "unit_cost": cost}]})


class _Actor:
    id = None


def _actor(app):
    """A real, all-branches user to act as, created once and reused (get-or-create)."""
    with app.app_context():
        from app.models.auth import User
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
        if u is None:
            from tests.helpers import make_user
            make_user(app, "actor", "Super Admin", all_branches=True)
            u = db.session.scalar(db.select(User).where(User.username == "actor"))
        return u


def test_po_number_generated_and_starts_as_draft(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id)
        db.session.commit()
        assert po.po_number.startswith("PO-") and po.status == "draft"
        po2 = _create_po(app, x, supplier_id)
        db.session.commit()
        assert po2.po_number != po.po_number


def test_submit_auto_approves_when_approval_not_required(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        assert settings_svc.get("purchasing.require_approval") is False
        po = _create_po(app, x, supplier_id)
        db.session.commit()
        svc.submit_po(_actor(app), po)
        db.session.commit()
        assert po.status == "approved"


def test_submit_requires_approval_when_setting_enabled(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        settings_svc.update({"purchasing.require_approval": "on"}, type("A", (), {"id": None})(),
                            keys=["purchasing.require_approval"])
        po = _create_po(app, x, supplier_id)
        db.session.commit()
        svc.submit_po(None, po)
        db.session.commit()
        assert po.status == "submitted"
        try:
            svc.receive_po(None, po, [{"po_line_id": po.lines[0].id, "quantity": "100"}])
            raise AssertionError
        except BusinessRuleError as e:
            assert "approval" in e.message
        svc.approve_po(_actor(app), po)
        db.session.commit()
        assert po.status == "approved"


def test_draft_can_only_be_edited_and_cancelled_freely(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id)
        db.session.commit()
        svc.update_po(None, po, {"lines": [{"item_id": x["beef"], "quantity": "500", "unit_cost": "0.01"}]})
        db.session.commit()
        assert po.lines[0].quantity_ordered == D("500.0000")
        svc.cancel_po(None, po, "changed my mind")
        db.session.commit()
        assert po.status == "cancelled"
        try:
            svc.update_po(None, po, {"lines": []})
            raise AssertionError
        except BusinessRuleError:
            pass


def test_receiving_creates_batch_movement_and_updates_po(purch):
    """The spec's own end-to-end promise for purchasing."""
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id, qty="10000", cost="0.008")
        db.session.commit()
        svc.submit_po(_actor(app), po)
        db.session.commit()
        before = inv_svc.current_balance(x["beef"], x["loc_a"])
        receipt = svc.receive_po(_actor(app), po, [
            {"po_line_id": po.lines[0].id, "quantity": "10000", "batch_no": "DELIVERY-1",
             "expiry_date": None}])
        db.session.commit()
        after = inv_svc.current_balance(x["beef"], x["loc_a"])
        assert after - before == D("10000.0000")
        batch = db.session.scalar(db.select(StockBatch).where(StockBatch.batch_no == "DELIVERY-1"))
        assert batch.unit_cost == D("0.008000") and batch.qty_remaining == D("10000.0000")
        assert po.status == "received" and po.lines[0].quantity_received == D("10000.0000")
        assert len(receipt.lines) == 1


def test_partial_receiving_across_two_deliveries(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id, qty="1000", cost="0.01")
        db.session.commit()
        svc.submit_po(_actor(app), po)
        db.session.commit()
        svc.receive_po(_actor(app), po, [{"po_line_id": po.lines[0].id, "quantity": "600"}])
        db.session.commit()
        assert po.status == "partially_received"
        svc.receive_po(_actor(app), po, [{"po_line_id": po.lines[0].id, "quantity": "400"}])
        db.session.commit()
        assert po.status == "received"
        assert inv_svc.current_balance(x["beef"], x["loc_a"]) == D("1000.0000")


def test_cannot_receive_more_than_outstanding(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id, qty="100", cost="0.01")
        db.session.commit()
        svc.submit_po(_actor(app), po)
        db.session.commit()
        try:
            svc.receive_po(_actor(app), po, [{"po_line_id": po.lines[0].id, "quantity": "150"}])
            raise AssertionError
        except ValidationError as e:
            assert "outstanding" in str(e.details).lower()
        db.session.rollback()
        assert inv_svc.current_balance(x["beef"], x["loc_a"]) == D("0.0000")


def test_receiving_before_approval_blocked(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id)
        db.session.commit()  # still draft
        try:
            svc.receive_po(_actor(app), po, [{"po_line_id": po.lines[0].id, "quantity": "10"}])
            raise AssertionError
        except BusinessRuleError:
            pass


def test_packaging_unit_conversion_on_po_line(purch):
    """Ordering '5 Box' of buns (1 Box = 24 pieces) should post 120 pieces at cost/24 each."""
    app, ids, x, supplier_id = purch
    with app.app_context():
        from app.models.inventory import ItemPackagingUnit
        box = ItemPackagingUnit(item_id=x["bun"], name="Box", factor_to_stock_unit=D("24"))
        db.session.add(box)
        db.session.commit()
        po = svc.create_po(_actor(app), {"supplier_id": supplier_id, "location_id": x["loc_a"],
                           "lines": [{"item_id": x["bun"], "quantity": "5", "unit_cost": "6.00",
                                     "packaging_unit_id": box.id}]})
        db.session.commit()
        line = po.lines[0]
        assert line.quantity_ordered == D("120.0000")  # 5 boxes * 24
        assert line.unit_cost == D("0.250000")  # $6/box / 24 = $0.25/piece
        svc.submit_po(_actor(app), po)
        svc.receive_po(_actor(app), po, [{"po_line_id": line.id, "quantity": "120"}])
        db.session.commit()
        assert inv_svc.current_balance(x["bun"], x["loc_a"]) == D("120.0000")


def test_invoice_and_payment_update_supplier_ledger(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id, qty="1000", cost="0.01")  # $10 of stock
        db.session.commit()
        svc.submit_po(_actor(app), po)
        svc.receive_po(_actor(app), po, [{"po_line_id": po.lines[0].id, "quantity": "1000"}])
        db.session.commit()
        assert svc.supplier_balance(supplier_id) == D("0.00")  # no invoice yet
        inv = svc.create_invoice(_actor(app), po, {"invoice_number": "INV-1", "subtotal": "10.00",
                                                    "tax_total": "1.60"})
        db.session.commit()
        assert inv.total == D("11.60") and svc.supplier_balance(supplier_id) == D("11.60")
        supplier = db.session.get(Supplier, supplier_id)
        svc.record_payment(_actor(app), supplier, {"amount": "5.00", "invoice_id": inv.id,
                                                    "branch_id": ids["A"]})
        db.session.commit()
        assert svc.supplier_balance(supplier_id) == D("6.60")
        assert db.session.get(PurchaseInvoice, inv.id).status == "partial"
        svc.record_payment(_actor(app), supplier, {"amount": "6.60", "invoice_id": inv.id,
                                                    "branch_id": ids["A"]})
        db.session.commit()
        assert svc.supplier_balance(supplier_id) == D("0.00")
        assert db.session.get(PurchaseInvoice, inv.id).status == "paid"


def test_duplicate_invoice_number_per_supplier_rejected(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id, qty="100", cost="0.01")
        db.session.commit()
        svc.submit_po(_actor(app), po)
        svc.receive_po(_actor(app), po, [{"po_line_id": po.lines[0].id, "quantity": "100"}])
        db.session.commit()
        svc.create_invoice(_actor(app), po, {"invoice_number": "DUP-1", "subtotal": "1.00"})
        db.session.commit()
        try:
            svc.create_invoice(_actor(app), po, {"invoice_number": "DUP-1", "subtotal": "2.00"})
            raise AssertionError
        except ValidationError as e:
            assert "already exists" in str(e.details)


def test_purchase_return_reduces_stock_and_supplier_balance(purch):
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id, qty="1000", cost="0.01")
        db.session.commit()
        svc.submit_po(_actor(app), po)
        svc.receive_po(_actor(app), po, [{"po_line_id": po.lines[0].id, "quantity": "1000"}])
        svc.create_invoice(_actor(app), po, {"invoice_number": "INV-9", "subtotal": "10.00"})
        db.session.commit()
        assert svc.supplier_balance(supplier_id) == D("10.00")
        ret = svc.create_return(_actor(app), {"supplier_id": supplier_id, "location_id": x["loc_a"],
                                              "reason": "Damaged in transit", "po_id": po.id,
                                              "lines": [{"item_id": x["beef"], "quantity": "100",
                                                        "unit_cost": "0.01"}]})
        db.session.commit()
        assert ret.total == D("1.00")
        assert inv_svc.current_balance(x["beef"], x["loc_a"]) == D("900.0000")
        assert svc.supplier_balance(supplier_id) == D("9.00")


def test_full_scenario_purchase_receive_sell_reconciles(purch):
    """Buy 10kg, define a 150g recipe, sell 20 -> exactly 3kg consumed; every number traceable."""
    app, ids, x, supplier_id = purch
    with app.app_context():
        from app.models.inventory import UnitOfMeasure
        from app.services import recipes as recipe_svc
        po = _create_po(app, x, supplier_id, qty="10000", cost="0.008")
        db.session.commit()
        svc.submit_po(_actor(app), po)
        svc.receive_po(_actor(app), po, [{"po_line_id": po.lines[0].id, "quantity": "10000",
                                          "batch_no": "B1"}])
        svc.create_invoice(_actor(app), po, {"invoice_number": "INV-100", "subtotal": "80.00"})
        db.session.commit()

        g_unit = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g"))
        burger = db.session.get(InventoryItem, x["burger"])
        recipe = recipe_svc.save(None, burger, {"yield_qty": "1",
                                                "lines": [{"item_id": x["beef"], "quantity": "150",
                                                          "unit_id": g_unit.id}]})
        db.session.commit()
        loc = db.session.get(__import__("app.models.inventory", fromlist=["Location"]).Location, x["loc_a"])
        recipe_svc.consume_for_output(recipe, loc, 20, actor=None, reference_type="sale")
        db.session.commit()

        assert inv_svc.current_balance(x["beef"], x["loc_a"]) == D("7000.0000")  # 10000 - 3000
        assert svc.supplier_balance(supplier_id) == D("80.00")  # unaffected by the sale
        # stock ledger reconciles
        from app.models.inventory import StockMovement
        total = db.session.scalar(db.select(db.func.sum(StockMovement.qty))
                                  .where(StockMovement.item_id == x["beef"]))
        assert total == inv_svc.current_balance(x["beef"], x["loc_a"])


def test_supplier_ledger_is_append_only(purch):
    import pytest
    from sqlalchemy.exc import DBAPIError
    app, ids, x, supplier_id = purch
    with app.app_context():
        po = _create_po(app, x, supplier_id, qty="10", cost="0.01")
        db.session.commit()
        svc.submit_po(_actor(app), po)
        svc.receive_po(_actor(app), po, [{"po_line_id": po.lines[0].id, "quantity": "10"}])
        svc.create_invoice(_actor(app), po, {"invoice_number": "INV-X", "subtotal": "0.10"})
        db.session.commit()
        with pytest.raises(DBAPIError, match="append-only"):
            db.session.execute(db.text("DELETE FROM supplier_ledger_entries"))
        db.session.rollback()
