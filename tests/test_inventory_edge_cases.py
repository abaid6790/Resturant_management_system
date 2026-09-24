from app.extensions import db
from app.models.inventory import InventoryItem, Location
from tests.helpers import login, make_role, make_user, post


def test_category_edit_and_deactivate(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, "/inventory/categories/new", {"name": "Drinks"})
    assert r.status_code == 302
    from app.models.inventory import InventoryCategory
    with app.app_context():
        cid = db.session.scalar(db.select(InventoryCategory.id).where(InventoryCategory.name == "Drinks"))
    assert post(c, f"/inventory/categories/{cid}/edit", {"name": "Beverages"}).status_code == 302
    assert post(c, f"/inventory/categories/{cid}/status", {"active": "0"}).status_code == 302
    with app.app_context():
        cat = db.session.get(InventoryCategory, cid)
        assert cat.name == "Beverages" and cat.is_active is False


def test_category_requires_a_name(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    assert post(c, "/inventory/categories/new", {"name": ""}).status_code == 422


def test_location_edit_and_status_toggle(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    assert post(c, f"/inventory/locations/{x['loc_a']}/edit",
               {"branch_id": ids["A"], "name": "Cold Store", "location_type": "warehouse"}).status_code == 302
    assert post(c, f"/inventory/locations/{x['loc_a']}/status", {"active": "0"}).status_code == 302
    with app.app_context():
        loc = db.session.get(Location, x["loc_a"])
        assert loc.name == "Cold Store" and loc.location_type == "warehouse" and not loc.is_active


def test_item_deactivate_reactivate(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    assert post(c, f"/inventory/items/{x['beef']}/status", {"active": "0"}).status_code == 302
    with app.app_context():
        assert db.session.get(InventoryItem, x["beef"]).is_active is False
    assert post(c, f"/inventory/items/{x['beef']}/status", {"active": "1"}).status_code == 302


def test_item_with_invalid_shelf_life_rejected(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, "/inventory/items/new", {"sku": "X9", "name": "X", "item_type": "ingredient",
             "stock_unit_id": x["g"], "min_stock": "0", "reorder_level": "0",
             "shelf_life_days": "not-a-number"})
    assert r.status_code == 422 and "shelf_life_days" in (r.get_json()["error"]["details"]
                                                          if r.is_json else r.get_data(as_text=True))


def test_item_with_bad_selling_price_rejected(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, "/inventory/items/new", {"sku": "X8", "name": "X", "item_type": "finished_product",
             "stock_unit_id": x["piece"], "min_stock": "0", "reorder_level": "0",
             "selling_price": "-5"})
    assert r.status_code == 422


def test_batches_page_and_expiry_sort(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    import datetime

    from app.services import inventory as inv_svc
    with app.app_context():
        item, loc = db.session.get(InventoryItem, x["beef"]), db.session.get(Location, x["loc_a"])
        inv_svc.receive_stock(item=item, location=loc, quantity="10", unit_cost="1", actor=None,
                              batch_no="SOON", expiry_date=datetime.date.today())
        inv_svc.receive_stock(item=item, location=loc, quantity="10", unit_cost="1", actor=None,
                              batch_no="LATER", expiry_date=datetime.date.today() + datetime.timedelta(days=30))
        db.session.commit()
    c = login(app, "boss")
    html = c.get("/inventory/stock/batches?expiring=1").get_data(as_text=True)
    assert html.index("SOON") < html.index("LATER")


def test_recipe_with_missing_ingredient_line_errors(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, f"/inventory/recipes/{x['burger']}", {"yield_qty": "1"})
    assert r.status_code == 422


def test_recipe_index_lists_items_with_and_without_recipes(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    from app.models.inventory import InventoryItem, UnitOfMeasure
    with app.app_context():
        g = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g")).id
        db.session.add(InventoryItem(sku="FRIES", name="Fries", item_type="finished_product",
                                     stock_unit_id=x["piece"], selling_price="3.50"))
        db.session.commit()
    post(c, f"/inventory/recipes/{x['burger']}", {"yield_qty": "1", "ing_item_id": [str(x["beef"])],
        "ing_qty": ["150"], "ing_unit_id": [str(g)]})
    html = c.get("/inventory/recipes/").get_data(as_text=True)
    assert "Classic Burger" in html  # has a recipe now
    assert "Fries" in html and "Add recipe" in html  # still needs one


def test_recipe_manage_permission_separate_from_view(inv):
    app, ids, x = inv
    make_role(app, "Recipe Editor", ["recipes.view", "recipes.manage", "products.view",
                                     "inventory.view", "dashboard.view"])
    make_user(app, "re", "Recipe Editor", branches=[ids["A"]])
    c = login(app, "re")
    from app.models.inventory import UnitOfMeasure
    with app.app_context():
        g = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g")).id
    r = post(c, f"/inventory/recipes/{x['burger']}", {"yield_qty": "1", "ing_item_id": [str(x["beef"])],
             "ing_qty": ["150"], "ing_unit_id": [str(g)]})
    assert r.status_code == 302
