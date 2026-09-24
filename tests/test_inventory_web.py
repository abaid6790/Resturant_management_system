from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryItem, Location
from tests.helpers import audit_actions, login, make_role, make_user, post


def test_stock_screens_render_for_someone_with_full_access(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    for url in ("/inventory/items/", "/inventory/recipes/", "/inventory/categories/",
               "/inventory/locations/", "/inventory/stock/", "/inventory/stock/ledger",
               "/inventory/stock/batches", "/inventory/stock/adjust", "/inventory/stock/transfer",
               "/inventory/stock/wastage", f"/inventory/items/{x['beef']}"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert "{{" not in r.get_data(as_text=True)


def test_adjust_transfer_wastage_end_to_end_through_http(inv):
    app, ids, x = inv
    make_user(app, "im", "Inventory Manager", branches=[ids["A"], ids["B"]])
    c = login(app, "im")
    r = post(c, "/inventory/stock/adjust", {"item_id": x["beef"], "location_id": x["loc_a"],
             "delta": "5000", "unit_cost": "0.01", "reason": "Opening stock"})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(InventoryItem)) >= 1
    r = post(c, "/inventory/stock/transfer", {"item_id": x["beef"], "from_location_id": x["loc_a"],
             "to_location_id": x["loc_b"], "quantity": "1000"})
    assert r.status_code == 302
    r = post(c, "/inventory/stock/wastage", {"item_id": x["beef"], "location_id": x["loc_b"],
             "quantity": "100", "reason": "Dropped"})
    assert r.status_code == 302
    from app.services.inventory import current_balance
    with app.app_context():
        assert current_balance(x["beef"], x["loc_a"]) == D("4000.0000")
        assert current_balance(x["beef"], x["loc_b"]) == D("900.0000")
    assert {"stock.adjustment_in", "stock.transfer_out", "stock.transfer_in",
           "stock.wastage"} <= set(audit_actions(app))


def test_adjustment_requires_a_reason(inv):
    app, ids, x = inv
    make_user(app, "im", "Inventory Manager", branches=[ids["A"]])
    c = login(app, "im")
    r = post(c, "/inventory/stock/adjust", {"item_id": x["beef"], "location_id": x["loc_a"],
             "delta": "10", "reason": ""})
    assert r.status_code == 422 and "reason" in r.get_data(as_text=True).lower()


def test_cashier_cannot_adjust_or_view_costs(inv):
    app, ids, x = inv
    make_user(app, "cash", "Cashier", branches=[ids["A"]])
    c = login(app, "cash")
    assert c.get("/inventory/stock/adjust").status_code == 403
    assert c.get("/inventory/items/").status_code == 403  # no products.view either


def test_waiter_can_view_items_but_not_manage(inv):
    app, ids, x = inv
    make_role(app, "Menu Viewer", ["products.view", "inventory.view", "dashboard.view"])
    make_user(app, "v", "Menu Viewer", branches=[ids["A"]])
    c = login(app, "v")
    assert c.get("/inventory/items/").status_code == 200
    assert "Add item" not in c.get("/inventory/items/").get_data(as_text=True)
    assert c.get("/inventory/items/new").status_code == 403
    assert c.get(f"/inventory/items/{x['beef']}/edit").status_code == 403


def test_view_costs_permission_hides_cost_columns(inv):
    app, ids, x = inv
    make_role(app, "Ops", ["inventory.view", "dashboard.view"])  # no inventory.view_costs
    make_user(app, "ops", "Ops", branches=[ids["A"]])
    from app.services import inventory as inv_svc
    with app.app_context():
        item, loc = db.session.get(InventoryItem, x["beef"]), db.session.get(Location, x["loc_a"])
        inv_svc.receive_stock(item=item, location=loc, quantity="100", unit_cost="0.01", actor=None)
        db.session.commit()
    html = login(app, "ops").get("/inventory/stock/").get_data(as_text=True)
    assert "Unit cost" not in html


def test_locations_and_stock_are_branch_scoped(inv):
    app, ids, x = inv
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    c = login(app, "mgr")
    html = c.get("/inventory/locations/").get_data(as_text=True)
    assert "Main Store" in html and html.count("Main Store") == 1  # only branch A's location
    r = post(c, "/inventory/stock/adjust", {"item_id": x["beef"], "location_id": x["loc_b"],
             "delta": "10", "reason": "sneaky"})
    assert r.status_code == 404  # location in another branch: not found, not a validation error


def test_low_stock_indicator_shown(inv):
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    from app.services import inventory as inv_svc
    with app.app_context():
        item, loc = db.session.get(InventoryItem, x["beef"]), db.session.get(Location, x["loc_a"])
        inv_svc.receive_stock(item=item, location=loc, quantity="200", unit_cost="0.01", actor=None)
        db.session.commit()  # min_stock=500, so 200 is Low
    html = login(app, "boss").get("/inventory/stock/").get_data(as_text=True)
    assert "Low" in html


def test_recipe_screen_shows_costing_and_respects_manage_permission(inv):
    app, ids, x = inv
    make_role(app, "Recipe Viewer", ["recipes.view", "products.view", "inventory.view",
                                     "dashboard.view"])
    make_user(app, "rv", "Recipe Viewer", branches=[ids["A"]])
    make_user(app, "boss", "Owner", all_branches=True)
    boss = login(app, "boss")
    from app.models.inventory import UnitOfMeasure
    with app.app_context():
        g = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "g")).id
        piece = db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == "piece")).id
    r = post(boss, f"/inventory/recipes/{x['burger']}", {
        "yield_qty": "1", "ing_item_id": [str(x["beef"]), str(x["bun"])],
        "ing_qty": ["150", "1"], "ing_unit_id": [str(g), str(piece)]})
    assert r.status_code == 302
    rv = login(app, "rv")
    html = rv.get(f"/inventory/recipes/{x['burger']}").get_data(as_text=True)
    assert "Save recipe" not in html and "disabled" in html
    assert post(rv, f"/inventory/recipes/{x['burger']}", {"yield_qty": "1"}).status_code == 403


def test_ledger_filter_dropdowns_render_with_a_selected_value(inv):
    """Regression: Jinja cannot resolve the bare `int` builtin, so filter dropdowns must not
    rely on `request.args.get(..., type=int)` inside a template expression."""
    app, ids, x = inv
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = c.get(f"/inventory/stock/ledger?item_id={x['beef']}&location_id={x['loc_a']}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f'value="{x["beef"]}" selected' in html
