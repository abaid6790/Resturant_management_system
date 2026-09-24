from app.extensions import db
from app.models.inventory import InventoryItem, Location
from tests.helpers import login, make_user, post


def test_item_sku_unique_and_validated(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    base = {"name": "Cheese", "item_type": "ingredient", "stock_unit_id": x["g"]}
    assert post(c, "/inventory/items/new", dict(base, sku="beef")).status_code == 422  # duplicate, case-insens.
    assert post(c, "/inventory/items/new", dict(base, sku="!")).status_code == 422
    assert post(c, "/inventory/items/new", dict(base, sku="CHEESE-1")).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(InventoryItem).where(InventoryItem.sku == "CHEESE-1"))


def test_max_stock_cannot_be_below_min(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, "/inventory/items/new", {"sku": "X1", "name": "X", "item_type": "ingredient",
             "stock_unit_id": x["g"], "min_stock": "10", "max_stock": "5"})
    assert r.status_code == 422 and "Maximum stock" in r.get_data(as_text=True)


def test_packaging_units_saved_and_deduplicated(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, f"/inventory/items/{x['bun']}/edit", {
        "name": "Burger bun", "item_type": "ingredient", "stock_unit_id": x["piece"],
        "min_stock": "10", "reorder_level": "20",
        "pkg_name": ["Box", "Box"], "pkg_factor": ["24", "48"]})
    assert r.status_code == 422  # service rejects the duplicate name before saving anything
    r = post(c, f"/inventory/items/{x['bun']}/edit", {
        "name": "Burger bun", "item_type": "ingredient", "stock_unit_id": x["piece"],
        "min_stock": "10", "reorder_level": "20", "pkg_name": ["Box"], "pkg_factor": ["24"]})
    assert r.status_code == 302
    with app.app_context():
        item = db.session.get(InventoryItem, x["bun"])
        assert [p.name for p in item.packaging_units] == ["Box"]


def test_track_batches_locked_once_item_has_movements(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    from app.services import inventory as inv_svc
    with app.app_context():
        item, loc = db.session.get(InventoryItem, x["beef"]), db.session.get(Location, x["loc_a"])
        inv_svc.receive_stock(item=item, location=loc, quantity="1000", unit_cost="0.01", actor=None)
        db.session.commit()
    r = post(c, f"/inventory/items/{x['beef']}/edit", {
        "name": "Beef mince", "item_type": "raw_material", "stock_unit_id": x["g"],
        "min_stock": "500", "reorder_level": "1000"})  # track_batches checkbox omitted = off
    assert r.status_code == 422 and "cannot be turned off" in r.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(InventoryItem, x["beef"]).track_batches is True  # nothing was changed


def test_location_scoped_to_branch_and_unique_name(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    assert post(c, "/inventory/locations/new", {"branch_id": ids["A"], "name": "Main Store",
               "location_type": "store"}).status_code == 422  # duplicate within branch
    assert post(c, "/inventory/locations/new", {"branch_id": ids["A"], "name": "Kitchen",
               "location_type": "kitchen"}).status_code == 302
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    mgr = login(app, "mgr")
    r = post(mgr, "/inventory/locations/new", {"branch_id": ids["B"], "name": "Sneaky",
             "location_type": "store"})
    assert r.status_code == 422 and "access" in r.get_data(as_text=True).lower()


def test_category_lifecycle(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    assert post(c, "/inventory/categories/new", {"name": "Meats"}).status_code == 422  # dup
    assert post(c, "/inventory/categories/new", {"name": "Beverages"}).status_code == 302
