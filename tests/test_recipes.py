"""
Recipe costing and consumption, including the spec's own exit scenario:
sell 20 products needing 150g each -> exactly 3kg deducted, with the right stock movements.
"""
from app.core.errors import BusinessRuleError, ValidationError
from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryItem, Location, StockMovement
from app.services import inventory as inv_svc
from app.services import recipes as svc


def _ids(x, *keys):
    return (x[k] for k in keys)


def test_recipe_cost_rolls_up_from_current_stock_cost(inv):
    app, ids, x = inv
    with app.app_context():
        beef, bun, burger = (db.session.get(InventoryItem, i) for i in (x["beef"], x["bun"], x["burger"]))
        loc = db.session.get(Location, x["loc_a"])
        inv_svc.receive_stock(item=beef, location=loc, quantity="5000", unit_cost="0.01", actor=None)
        inv_svc.receive_stock(item=bun, location=loc, quantity="100", unit_cost="0.30", actor=None)
        from app.models.inventory import UnitOfMeasure
        g_unit = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g"))
        piece = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "piece"))
        recipe = svc.save(None, burger, {"yield_qty": "1", "lines": [
            {"item_id": beef.id, "quantity": "150", "unit_id": g_unit.id},
            {"item_id": bun.id, "quantity": "1", "unit_id": piece.id},
        ]})
        db.session.commit()
        cost = svc.cost_of_recipe(recipe, loc)
        # 150g * 0.01/g = 1.50, + 1 bun * 0.30 = 1.80
        assert cost == D("1.80")
        summary = svc.costing_summary(recipe, loc)
        assert summary["selling_price"] == D("8.99")
        assert summary["gross_profit"] == D("7.19")


def test_sell_20_products_consumes_exactly_3kg(inv):
    """The scenario named explicitly in the spec."""
    app, ids, x = inv
    with app.app_context():
        beef, bun, burger = (db.session.get(InventoryItem, i) for i in (x["beef"], x["bun"], x["burger"]))
        loc = db.session.get(Location, x["loc_a"])
        inv_svc.receive_stock(item=beef, location=loc, quantity="10000", unit_cost="0.008",
                              batch_no="PURCHASE-1", actor=None)
        inv_svc.receive_stock(item=bun, location=loc, quantity="50", unit_cost="0.25", actor=None)
        from app.models.inventory import UnitOfMeasure
        g_unit = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g"))
        piece = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "piece"))
        recipe = svc.save(None, burger, {"yield_qty": "1", "lines": [
            {"item_id": beef.id, "quantity": "150", "unit_id": g_unit.id},
            {"item_id": bun.id, "quantity": "1", "unit_id": piece.id},
        ]})
        db.session.commit()

        before = inv_svc.current_balance(beef.id, loc.id)
        moves = svc.consume_for_output(recipe, loc, 20, actor=None, reference_type="sale",
                                       reference_id="ORDER-1")
        db.session.commit()
        after = inv_svc.current_balance(beef.id, loc.id)

        assert before - after == D("3000.0000")  # exactly 3kg, per the spec's own example
        assert inv_svc.current_balance(bun.id, loc.id) == D("30.0000")  # 50 - 20
        beef_moves = [m for m in moves if m.item_id == beef.id]
        assert sum(-m.qty for m in beef_moves) == D("3000.0000")
        assert all(m.reference_type == "sale" and m.reference_id == "ORDER-1" for m in moves)
        # every movement traces back to the PURCHASE-1 batch at the cost it was bought at
        ledger = db.session.scalars(db.select(StockMovement).where(StockMovement.item_id == beef.id,
                                                                    StockMovement.movement_type == "consumption"))
        assert all(m.unit_cost == D("0.008000") for m in ledger)


def test_insufficient_stock_blocks_the_whole_sale(inv):
    app, ids, x = inv
    with app.app_context():
        beef, bun, burger = (db.session.get(InventoryItem, i) for i in (x["beef"], x["bun"], x["burger"]))
        loc = db.session.get(Location, x["loc_a"])
        inv_svc.receive_stock(item=beef, location=loc, quantity="1000", unit_cost="0.01", actor=None)
        inv_svc.receive_stock(item=bun, location=loc, quantity="2", unit_cost="0.25", actor=None)  # only 2 buns
        from app.models.inventory import UnitOfMeasure
        g_unit = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g"))
        piece = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "piece"))
        recipe = svc.save(None, burger, {"yield_qty": "1", "lines": [
            {"item_id": beef.id, "quantity": "150", "unit_id": g_unit.id},
            {"item_id": bun.id, "quantity": "1", "unit_id": piece.id},
        ]})
        db.session.commit()
        try:
            svc.consume_for_output(recipe, loc, 5, actor=None)  # needs 5 buns, only 2 available
            raise AssertionError("should have raised")
        except BusinessRuleError:
            db.session.rollback()
        # nothing was deducted: beef is untouched because the transaction rolled back
        assert inv_svc.current_balance(beef.id, loc.id) == D("1000.0000")


def test_recipe_converts_units_correctly(inv):
    """1kg in the recipe must deduct exactly 1000g from a gram-tracked ingredient."""
    app, ids, x = inv
    with app.app_context():
        beef, burger = db.session.get(InventoryItem, x["beef"]), db.session.get(InventoryItem, x["burger"])
        loc = db.session.get(Location, x["loc_a"])
        inv_svc.receive_stock(item=beef, location=loc, quantity="5000", unit_cost="0.01", actor=None)
        from app.models.inventory import UnitOfMeasure
        kg_unit = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "kg"))
        recipe = svc.save(None, burger, {"yield_qty": "1",
                                         "lines": [{"item_id": beef.id, "quantity": "1", "unit_id": kg_unit.id}]})
        db.session.commit()
        svc.consume_for_output(recipe, loc, 1, actor=None)
        db.session.commit()
        assert inv_svc.current_balance(beef.id, loc.id) == D("4000.0000")


def test_incompatible_unit_kind_rejected(inv):
    app, ids, x = inv
    with app.app_context():
        beef, burger = db.session.get(InventoryItem, x["beef"]), db.session.get(InventoryItem, x["burger"])
        from app.models.inventory import UnitOfMeasure
        piece = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "piece"))
        try:
            svc.save(None, burger, {"yield_qty": "1",
                                    "lines": [{"item_id": beef.id, "quantity": "1", "unit_id": piece.id}]})
            raise AssertionError
        except ValidationError as e:
            assert "matching unit" in str(e.details)


def test_recipe_cannot_use_itself_or_duplicate_ingredients(inv):
    app, ids, x = inv
    with app.app_context():
        beef, burger = db.session.get(InventoryItem, x["beef"]), db.session.get(InventoryItem, x["burger"])
        from app.models.inventory import UnitOfMeasure
        g = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g"))
        import pytest
        with pytest.raises(ValidationError):
            svc.save(None, burger, {"yield_qty": "1",
                                    "lines": [{"item_id": burger.id, "quantity": "1", "unit_id": g.id}]})
        with pytest.raises(ValidationError):
            svc.save(None, burger, {"yield_qty": "1", "lines": [
                {"item_id": beef.id, "quantity": "1", "unit_id": g.id},
                {"item_id": beef.id, "quantity": "2", "unit_id": g.id}]})


def test_sub_recipe_cost_and_consumption_recurse(inv):
    """Semi-finished item: a sauce with its own recipe, used as an ingredient in the burger."""
    app, ids, x = inv
    with app.app_context():
        from app.models.inventory import UnitOfMeasure
        g = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g"))
        burger = db.session.get(InventoryItem, x["burger"])
        loc = db.session.get(Location, x["loc_a"])
        sauce = InventoryItem(sku="SAUCE", name="House Sauce", item_type="semi_finished",
                              stock_unit_id=x["g"], track_batches=True)
        tomato = InventoryItem(sku="TOMATO", name="Tomato", item_type="raw_material",
                               stock_unit_id=x["g"], track_batches=True)
        db.session.add_all([sauce, tomato])
        db.session.commit()
        inv_svc.receive_stock(item=tomato, location=loc, quantity="2000", unit_cost="0.005", actor=None)
        svc.save(None, sauce, {"yield_qty": "100",
                               "lines": [{"item_id": tomato.id, "quantity": "100", "unit_id": g.id}]})
        burger_recipe = svc.save(None, burger, {"yield_qty": "1",
                                                "lines": [{"item_id": sauce.id, "quantity": "20", "unit_id": g.id}]})
        db.session.commit()
        # sauce costs 0.005/g -> burger needs 20g of sauce -> 0.10
        assert svc.cost_of_recipe(burger_recipe, loc) == D("0.10")
        svc.consume_for_output(burger_recipe, loc, 5, actor=None)  # no sauce in stock: made on the fly
        db.session.commit()
        assert inv_svc.current_balance(tomato.id, loc.id) == D("1900.0000")  # 5 * 20g = 100g of sauce


def test_circular_recipe_detected(inv):
    app, ids, x = inv
    with app.app_context():
        from app.models.inventory import UnitOfMeasure
        g = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g"))
        a = InventoryItem(sku="A1", name="A", item_type="semi_finished", stock_unit_id=x["g"])
        b = InventoryItem(sku="B1", name="B", item_type="semi_finished", stock_unit_id=x["g"])
        db.session.add_all([a, b])
        db.session.commit()
        svc.save(None, a, {"yield_qty": "1", "lines": [{"item_id": b.id, "quantity": "1", "unit_id": g.id}]})
        ra = svc.save(None, b, {"yield_qty": "1", "lines": [{"item_id": a.id, "quantity": "1", "unit_id": g.id}]})
        db.session.commit()
        loc = db.session.get(Location, x["loc_a"])
        try:
            svc.cost_of_recipe(ra, loc)
            raise AssertionError
        except BusinessRuleError as e:
            assert "Circular" in e.message
