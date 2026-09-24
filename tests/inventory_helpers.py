from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryCategory, InventoryItem, Location, UnitOfMeasure
from app.services import bootstrap


def unit(app, code):
    with app.app_context():
        return db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == code))


def setup_inventory(app, branch_ids):
    with app.app_context():
        bootstrap.seed_roles()  # also seeds standard units
        g_id = unit(app, "g").id
        piece_id = unit(app, "piece").id
        loc_a = Location(branch_id=branch_ids["A"], name="Main Store", location_type="store")
        loc_b = Location(branch_id=branch_ids["B"], name="Main Store", location_type="store")
        cat = InventoryCategory(name="Meats")
        beef = InventoryItem(sku="BEEF", name="Beef mince", item_type="raw_material", category=cat,
                             stock_unit_id=g_id, track_batches=True, min_stock=D("500"),
                             reorder_level=D("1000"))
        bun = InventoryItem(sku="BUN", name="Burger bun", item_type="ingredient", stock_unit_id=piece_id,
                            track_batches=True, min_stock=D("10"), reorder_level=D("20"))
        burger = InventoryItem(sku="BURGER", name="Classic Burger", item_type="finished_product",
                               stock_unit_id=piece_id, track_batches=False, selling_price=D("8.99"))
        db.session.add_all([loc_a, loc_b, cat, beef, bun, burger])
        db.session.commit()
        return {"loc_a": loc_a.id, "loc_b": loc_b.id, "beef": beef.id, "bun": bun.id,
                "burger": burger.id, "g": g_id, "kg": unit(app, "kg").id, "piece": piece_id}


def purchasing_setup(app, branch_ids):
    """Adds a supplier on top of setup_inventory's items/locations."""
    from app.models.purchasing import Supplier
    with app.app_context():
        supplier = Supplier(code="ACME", name="Acme Foods", payment_terms_days=14)
        db.session.add(supplier)
        db.session.commit()
        return supplier.id
