from app.extensions import db
from app.models.purchasing import Supplier
from tests.helpers import login, make_user, post


def test_supplier_code_unique_and_validated(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    base = {"name": "Fresh Farms", "payment_terms_days": "7"}
    assert post(c, "/purchasing/suppliers/new", dict(base, code="acme")).status_code == 422  # dup, ci
    assert post(c, "/purchasing/suppliers/new", dict(base, code="!!")).status_code == 422
    assert post(c, "/purchasing/suppliers/new", dict(base, code="FRESH")).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Supplier).where(Supplier.code == "FRESH"))


def test_supplier_edit_and_deactivate(purch):
    app, ids, x, supplier_id = purch
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, f"/purchasing/suppliers/{supplier_id}/edit",
            {"code": "ACME", "name": "Acme Foods Ltd", "payment_terms_days": "30"})
    assert r.status_code == 302
    assert post(c, f"/purchasing/suppliers/{supplier_id}/status", {"active": "0"}).status_code == 302
    with app.app_context():
        s = db.session.get(Supplier, supplier_id)
        assert s.name == "Acme Foods Ltd" and s.payment_terms_days == 30 and s.is_active is False


def test_supplier_permission_gates(purch):
    app, ids, x, supplier_id = purch
    from tests.helpers import make_role
    make_role(app, "Read Only Purchasing", ["suppliers.view", "purchases.view", "dashboard.view"])
    make_user(app, "ro", "Read Only Purchasing", branches=[ids["A"]])
    c = login(app, "ro")
    assert c.get("/purchasing/suppliers/").status_code == 200
    assert c.get("/purchasing/suppliers/new").status_code == 403
    assert c.get(f"/purchasing/suppliers/{supplier_id}/edit").status_code == 403
