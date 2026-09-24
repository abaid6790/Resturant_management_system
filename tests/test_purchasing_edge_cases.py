from app.core.errors import BusinessRuleError, ValidationError
from app.core.money import D
from app.extensions import db
from app.models.purchasing import PurchaseOrder
from tests.helpers import login, make_user, post


def _actor(app):
    with app.app_context():
        from app.models.auth import User
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
        if u is None:
            from tests.helpers import make_user as _mk
            _mk(app, "actor", "Super Admin", all_branches=True)
            u = db.session.scalar(db.select(User).where(User.username == "actor"))
        return u


def test_po_line_validation(purch):
    app, ids, x, supplier_id = purch
    from app.services import purchasing as svc
    with app.app_context():
        actor = _actor(app)
        try:
            svc.create_po(actor, {"supplier_id": str(supplier_id), "location_id": str(x["loc_a"]),
                                  "lines": [{"item_id": str(x["beef"]), "quantity": "0",
                                            "unit_cost": "0.01"}]})
            raise AssertionError
        except ValidationError as e:
            assert "line0" in e.details
        try:
            svc.create_po(actor, {"supplier_id": str(supplier_id), "location_id": str(x["loc_a"]),
                                  "lines": [{"item_id": str(x["beef"]), "quantity": "10",
                                            "unit_cost": "0.01"},
                                           {"item_id": str(x["beef"]), "quantity": "5",
                                            "unit_cost": "0.02"}]})
            raise AssertionError
        except ValidationError as e:
            assert "listed twice" in str(e.details)
        try:
            svc.create_po(actor, {"supplier_id": str(supplier_id), "location_id": str(x["loc_a"]),
                                  "lines": []})
            raise AssertionError
        except ValidationError as e:
            assert "lines" in e.details


def test_inactive_supplier_rejected(purch):
    app, ids, x, supplier_id = purch
    from app.models.purchasing import Supplier
    from app.services import purchasing as svc
    with app.app_context():
        db.session.get(Supplier, supplier_id).is_active = False
        db.session.commit()
        try:
            svc.create_po(_actor(app), {"supplier_id": str(supplier_id), "location_id": str(x["loc_a"]),
                                        "lines": [{"item_id": str(x["beef"]), "quantity": "1",
                                                  "unit_cost": "1"}]})
            raise AssertionError
        except ValidationError as e:
            assert "active" in str(e.details).lower()


def test_cancel_and_reject_wrong_status_blocked(purch):
    app, ids, x, supplier_id = purch
    from app.services import purchasing as svc
    with app.app_context():
        actor = _actor(app)
        po = svc.create_po(actor, {"supplier_id": str(supplier_id), "location_id": str(x["loc_a"]),
                           "lines": [{"item_id": str(x["beef"]), "quantity": "10", "unit_cost": "1"}]})
        db.session.commit()
        try:
            svc.approve_po(actor, po)
            raise AssertionError
        except BusinessRuleError:
            pass
        try:
            svc.reject_po(actor, po, "no reason needed test")
            raise AssertionError
        except BusinessRuleError:
            pass
        svc.submit_po(actor, po)  # -> approved (no approval required)
        db.session.commit()
        try:
            svc.reject_po(actor, po, "too late")  # can't reject something already approved
            raise AssertionError
        except BusinessRuleError:
            pass


def test_cannot_cancel_after_receiving(purch):
    app, ids, x, supplier_id = purch
    from app.services import purchasing as svc
    with app.app_context():
        actor = _actor(app)
        po = svc.create_po(actor, {"supplier_id": str(supplier_id), "location_id": str(x["loc_a"]),
                           "lines": [{"item_id": str(x["beef"]), "quantity": "10", "unit_cost": "1"}]})
        db.session.commit()
        svc.submit_po(actor, po)
        svc.receive_po(actor, po, [{"po_line_id": po.lines[0].id, "quantity": "5"}])
        db.session.commit()
        try:
            svc.cancel_po(actor, po, "too late")
            raise AssertionError
        except BusinessRuleError as e:
            assert "received" in e.message.lower()


def test_invoice_validation_errors(purch):
    app, ids, x, supplier_id = purch
    from app.services import purchasing as svc
    with app.app_context():
        actor = _actor(app)
        po = svc.create_po(actor, {"supplier_id": str(supplier_id), "location_id": str(x["loc_a"]),
                           "lines": [{"item_id": str(x["beef"]), "quantity": "10", "unit_cost": "1"}]})
        db.session.commit()
        svc.submit_po(actor, po)
        svc.receive_po(actor, po, [{"po_line_id": po.lines[0].id, "quantity": "10"}])
        db.session.commit()
        try:
            svc.create_invoice(actor, po, {"invoice_number": "", "subtotal": "10"})
            raise AssertionError
        except ValidationError as e:
            assert "invoice_number" in e.details
        try:
            svc.create_invoice(actor, po, {"invoice_number": "OK-1", "subtotal": "5",
                                           "discount_total": "50"})  # discount exceeds subtotal
            raise AssertionError
        except ValidationError as e:
            assert "discount_total" in e.details


def test_payment_validation_errors(purch):
    app, ids, x, supplier_id = purch
    from app.models.purchasing import Supplier
    from app.services import purchasing as svc
    with app.app_context():
        actor = _actor(app)
        supplier = db.session.get(Supplier, supplier_id)
        try:
            svc.record_payment(actor, supplier, {"amount": "0"})
            raise AssertionError
        except ValidationError as e:
            assert "amount" in e.details
        try:
            svc.record_payment(actor, supplier, {"amount": "10", "invoice_id": "999999"})
            raise AssertionError
        except ValidationError as e:
            assert "invoice_id" in e.details
        try:
            svc.record_payment(actor, supplier, {"amount": "10", "method": "bitcoin"})
            raise AssertionError
        except ValidationError as e:
            assert "method" in e.details


def test_return_line_validation(purch):
    app, ids, x, supplier_id = purch
    from app.services import purchasing as svc
    with app.app_context():
        actor = _actor(app)
        try:
            svc.create_return(actor, {"supplier_id": str(supplier_id), "location_id": str(x["loc_a"]),
                                      "reason": "", "lines": [{"item_id": str(x["beef"]),
                                      "quantity": "1", "unit_cost": "1"}]})
            raise AssertionError
        except ValidationError as e:
            assert "reason" in e.details
        try:
            svc.create_return(actor, {"supplier_id": str(supplier_id), "location_id": str(x["loc_a"]),
                                      "reason": "test", "lines": []})
            raise AssertionError
        except ValidationError as e:
            assert "lines" in e.details


def test_po_list_and_supplier_filters_through_http(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/purchasing/orders/new", {"supplier_id": supplier_id, "location_id": x["loc_a"],
        "line_item_id": [str(x["beef"])], "line_qty": ["10"], "line_cost": ["1.00"]})
    html = c.get("/purchasing/orders/?status=draft").get_data(as_text=True)
    assert "PO-" in html
    html2 = c.get(f"/purchasing/orders/?supplier_id={supplier_id}").get_data(as_text=True)
    assert "PO-" in html2
    assert "No purchase orders" in c.get("/purchasing/orders/?status=cancelled").get_data(as_text=True)


def test_edit_locked_once_submitted(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "im", "Inventory Manager", branches=[ids["A"]])
    c = login(app, "im")
    post(c, "/purchasing/orders/new", {"supplier_id": supplier_id, "location_id": x["loc_a"],
        "line_item_id": [str(x["beef"])], "line_qty": ["10"], "line_cost": ["1.00"]})
    with app.app_context():
        po_id = db.session.scalar(db.select(PurchaseOrder.id))
    post(c, f"/purchasing/orders/{po_id}/submit")
    r = post(c, f"/purchasing/orders/{po_id}/edit", {"line_item_id": [str(x["beef"])],
             "line_qty": ["20"], "line_cost": ["1.00"]})
    assert r.status_code == 422
    with app.app_context():
        assert db.session.get(PurchaseOrder, po_id).lines[0].quantity_ordered == D("10.0000")
