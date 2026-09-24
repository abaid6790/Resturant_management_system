import pytest

from app.core.permissions import ALL_CODES, CATALOG, DEFAULT_ROLES, SENSITIVE
from tests.helpers import login, make_role, make_user, post


def test_catalogue_integrity():
    codes = [c for items in CATALOG.values() for c, _, _ in items]
    assert len(codes) == len(set(codes)), "duplicate permission codes"
    for name, spec in DEFAULT_ROLES.items():
        assert set(spec["permissions"]) <= ALL_CODES, f"{name} references unknown permissions"
    assert {"pos.refund", "inventory.adjust", "purchases.approve", "reports.financial",
            "users.manage", "roles.manage", "backup.restore"} <= SENSITIVE
    cashier = set(DEFAULT_ROLES["Cashier"]["permissions"])
    assert not cashier & SENSITIVE  # cashiers never get sensitive permissions by default


@pytest.mark.parametrize("url", ["/admin/users", "/admin/roles", "/admin/branches", "/admin/settings",
                                 "/admin/audit", "/admin/users/new", "/admin/roles/new"])
def test_cashier_is_blocked_by_the_backend(env, url):
    app, ids = env
    make_user(app, "cash", "Cashier", branches=[ids["A"]])
    assert login(app, "cash").get(url).status_code == 403


def test_hidden_menu_is_not_the_only_defence(env):
    app, ids = env
    make_user(app, "cash", "Cashier", branches=[ids["A"]])
    c = login(app, "cash")
    html = c.get("/").get_data(as_text=True)
    assert "/admin/users" not in html and "/admin/roles" not in html
    assert post(c, "/admin/users/new", {"username": "x"}).status_code == 403
    assert post(c, "/admin/roles/new", {"name": "Evil", "permissions": ["users.manage"]}).status_code == 403
    assert post(c, "/admin/settings/", {"restaurant.name": "Hacked"}).status_code == 403


def test_view_permission_is_not_manage_permission(env):
    app, ids = env
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])  # has users.view, not users.manage
    c = login(app, "mgr")
    assert c.get("/admin/users").status_code == 200
    assert c.get("/admin/users/new").status_code == 403
    assert "Add user" not in c.get("/admin/users").get_data(as_text=True)


def test_accountant_can_read_audit_but_not_settings(env):
    app, ids = env
    make_user(app, "acct", "Accountant", branches=[ids["A"]])
    c = login(app, "acct")
    assert c.get("/admin/audit").status_code == 200
    assert c.get("/admin/settings").status_code == 403


def test_permission_changes_apply_immediately(env):
    app, ids = env
    make_user(app, "cash", "Cashier", branches=[ids["A"]])
    make_user(app, "boss", "Owner", all_branches=True)
    cash, boss = login(app, "cash"), login(app, "boss")
    assert cash.get("/admin/audit").status_code == 403
    from app.extensions import db
    from app.models.auth import Role
    with app.app_context():
        role = db.session.scalar(db.select(Role).where(Role.name == "Cashier"))
        rid, perms = role.id, sorted(p.code for p in role.permissions) + ["audit.view"]
    r = post(boss, f"/admin/roles/{rid}/edit", {"name": "Cashier", "permissions": perms})
    assert r.status_code == 302
    assert cash.get("/admin/audit").status_code == 200  # no re-login needed


def test_custom_role_can_be_built_and_used(env):
    app, ids = env
    make_role(app, "Stock Auditor", ["inventory.view", "audit.view", "dashboard.view"])
    make_user(app, "aud", "Stock Auditor", branches=[ids["A"]])
    me = login(app, "aud").get("/api/v1/me").get_json()["data"]
    assert me["permissions"] == ["audit.view", "dashboard.view", "inventory.view"]
