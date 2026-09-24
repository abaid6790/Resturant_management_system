from app.extensions import db
from app.models.purchasing import PurchaseOrder
from tests.helpers import login, make_role, make_user, post


def test_full_po_lifecycle_through_http(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "im", "Inventory Manager", branches=[ids["A"]])
    make_user(app, "boss", "Owner", all_branches=True)
    im, boss = login(app, "im"), login(app, "boss")

    r = post(im, "/purchasing/orders/new", {"supplier_id": supplier_id, "location_id": x["loc_a"],
             "line_item_id": [str(x["beef"])], "line_qty": ["1000"], "line_cost": ["0.008"]})
    assert r.status_code == 302
    with app.app_context():
        po_id = db.session.scalar(db.select(PurchaseOrder.id))
    assert post(im, f"/purchasing/orders/{po_id}/submit").status_code == 302
    with app.app_context():
        assert db.session.get(PurchaseOrder, po_id).status == "approved"  # not required by default
        line_id = db.session.get(PurchaseOrder, po_id).lines[0].id

    r = post(im, f"/purchasing/orders/{po_id}/receive", {"recv_line_id": [str(line_id)],
             "recv_qty": ["1000"], "recv_cost": ["0.008"], "recv_batch": ["B1"]})
    assert r.status_code == 302
    from app.services.inventory import current_balance
    with app.app_context():
        assert current_balance(x["beef"], x["loc_a"]) == 1000

    # invoicing/paying needs purchases.pay, which Inventory Manager does not have
    assert post(im, f"/purchasing/orders/{po_id}/invoice",
               {"invoice_number": "I1", "subtotal": "8.00"}).status_code == 403
    assert post(boss, f"/purchasing/orders/{po_id}/invoice",
               {"invoice_number": "I1", "subtotal": "8.00"}).status_code == 302
    from app.services.purchasing import supplier_balance
    with app.app_context():
        assert supplier_balance(supplier_id) == 8


def test_approval_required_workflow_through_http(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "boss", "Owner", all_branches=True)
    make_role(app, "PO Creator", ["purchases.view", "purchases.create", "dashboard.view"])
    make_user(app, "creator", "PO Creator", branches=[ids["A"]])
    boss, creator = login(app, "boss"), login(app, "creator")
    with app.app_context():
        from app.services import settings as settings_svc
        settings_svc.update({"purchasing.require_approval": "on"}, type("A", (), {"id": None})(),
                            keys=["purchasing.require_approval"])
        db.session.commit()

    post(creator, "/purchasing/orders/new", {"supplier_id": supplier_id, "location_id": x["loc_a"],
        "line_item_id": [str(x["beef"])], "line_qty": ["100"], "line_cost": ["0.01"]})
    with app.app_context():
        po_id = db.session.scalar(db.select(PurchaseOrder.id))
    assert post(creator, f"/purchasing/orders/{po_id}/submit").status_code == 302
    with app.app_context():
        assert db.session.get(PurchaseOrder, po_id).status == "submitted"
    # creator has no purchases.approve
    assert post(creator, f"/purchasing/orders/{po_id}/approve").status_code == 403
    assert post(boss, f"/purchasing/orders/{po_id}/approve").status_code == 302
    with app.app_context():
        assert db.session.get(PurchaseOrder, po_id).status == "approved"


def test_rejecting_a_po_requires_a_reason(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "boss", "Owner", all_branches=True)
    with app.app_context():
        from app.services import settings as settings_svc
        settings_svc.update({"purchasing.require_approval": "on"}, type("A", (), {"id": None})(),
                            keys=["purchasing.require_approval"])
        db.session.commit()
    c = login(app, "boss")
    post(c, "/purchasing/orders/new", {"supplier_id": supplier_id, "location_id": x["loc_a"],
        "line_item_id": [str(x["beef"])], "line_qty": ["10"], "line_cost": ["1.00"]})
    with app.app_context():
        po_id = db.session.scalar(db.select(PurchaseOrder.id))
    post(c, f"/purchasing/orders/{po_id}/submit")
    r = post(c, f"/purchasing/orders/{po_id}/reject", {"reason": ""})
    assert r.status_code == 302  # redirects back to the PO with a flashed error, not a 422
    with app.app_context():
        assert db.session.get(PurchaseOrder, po_id).status == "submitted"  # unchanged
    post(c, f"/purchasing/orders/{po_id}/reject", {"reason": "Prices too high"})
    with app.app_context():
        po = db.session.get(PurchaseOrder, po_id)
        assert po.status == "rejected" and po.rejected_reason == "Prices too high"


def test_purchasing_is_branch_scoped(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    make_user(app, "boss", "Owner", all_branches=True)
    boss = login(app, "boss")
    post(boss, "/purchasing/orders/new", {"supplier_id": supplier_id, "location_id": x["loc_b"],
        "line_item_id": [str(x["beef"])], "line_qty": ["10"], "line_cost": ["1.00"]})
    with app.app_context():
        po_id = db.session.scalar(db.select(PurchaseOrder.id))
    mgr = login(app, "mgr")
    assert mgr.get(f"/purchasing/orders/{po_id}").status_code == 404  # existing PO, hidden by branch
    # location B is outside the manager's branch: treated as an invalid form choice (422),
    # the same way an unknown item or supplier id would be, not as a 404 (this isn't a URL lookup)
    r = post(mgr, "/purchasing/orders/new", {"supplier_id": supplier_id, "location_id": x["loc_b"],
             "line_item_id": [str(x["beef"])], "line_qty": ["10"], "line_cost": ["1.00"]})
    assert r.status_code == 422


def test_return_flow_through_http(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "im", "Inventory Manager", branches=[ids["A"]])
    c = login(app, "im")
    post(c, "/purchasing/orders/new", {"supplier_id": supplier_id, "location_id": x["loc_a"],
        "line_item_id": [str(x["beef"])], "line_qty": ["500"], "line_cost": ["0.01"]})
    with app.app_context():
        po = db.session.scalar(db.select(PurchaseOrder))
        po_id, line_id = po.id, po.lines[0].id
    post(c, f"/purchasing/orders/{po_id}/submit")
    post(c, f"/purchasing/orders/{po_id}/receive",
        {"recv_line_id": [str(line_id)], "recv_qty": ["500"], "recv_cost": ["0.01"]})
    r = post(c, "/purchasing/orders/returns/new", {"supplier_id": supplier_id,
             "location_id": x["loc_a"], "reason": "Wrong item", "ret_item_id": [str(x["beef"])],
             "ret_qty": ["50"], "ret_cost": ["0.01"]})
    assert r.status_code == 302
    from app.services.inventory import current_balance
    with app.app_context():
        assert current_balance(x["beef"], x["loc_a"]) == 450


def test_pages_render_without_template_errors(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/purchasing/orders/new", {"supplier_id": supplier_id, "location_id": x["loc_a"],
        "line_item_id": [str(x["beef"])], "line_qty": ["10"], "line_cost": ["1.00"]})
    with app.app_context():
        po_id = db.session.scalar(db.select(PurchaseOrder.id))
    for url in ("/purchasing/suppliers/", "/purchasing/orders/", "/purchasing/orders/new",
               "/purchasing/orders/returns/new", f"/purchasing/orders/{po_id}",
               f"/purchasing/suppliers/{supplier_id}"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert "{{" not in r.get_data(as_text=True)
