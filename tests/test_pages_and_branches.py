import pytest

from app.extensions import db
from app.models.auth import Branch, Role
from tests.helpers import audit_actions, login, make_user, post


@pytest.fixture()
def admin(env):
    app, ids = env
    uid = make_user(app, "root", "Super Admin", all_branches=True)
    return app, ids, uid, login(app, "root")


def test_every_screen_renders_for_a_super_admin(admin):
    app, ids, uid, c = admin
    with app.app_context():
        rid = db.session.scalar(db.select(Role.id).where(Role.name == "Cashier"))
    pages = ["/", "/admin/users", "/admin/users/new", f"/admin/users/{uid}/edit", "/admin/roles",
             "/admin/roles/new", f"/admin/roles/{rid}/edit", "/admin/branches", "/admin/branches/new",
             f"/admin/branches/{ids['A']}/edit", "/admin/settings", "/admin/audit", "/account/password"]
    for url in pages:
        r = c.get(url)
        html = r.get_data(as_text=True)
        assert r.status_code == 200, url
        assert "{{" not in html and "{%" not in html, f"unrendered template syntax on {url}"
        assert 'name="csrf-token"' in html
    assert c.get("/admin/users?q=root&status=active").status_code == 200
    users_html = c.get("/admin/users").get_data(as_text=True)
    assert "Deactivate" not in users_html.split("root")[1].split("</tr>")[0]  # no self-deactivate button
    assert "Sign out" in c.get("/").get_data(as_text=True) and "Branch A" in c.get("/").get_data(as_text=True)


def test_nav_only_lists_what_the_user_may_open(env):
    app, ids = env
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    html = login(app, "mgr").get("/").get_data(as_text=True)
    assert "/admin/users" in html and "/admin/audit" in html
    assert "/admin/roles" not in html and "/admin/branches" not in html


def test_branch_lifecycle(admin):
    app, ids, _, c = admin
    assert post(c, "/admin/branches/new", {"code": "dha-1", "name": "DHA", "phone": "111"}).status_code == 302
    assert post(c, "/admin/branches/new", {"code": "DHA-1", "name": "Dup"}).status_code == 422  # code unique
    assert post(c, "/admin/branches/new", {"code": "!", "name": ""}).status_code == 422
    with app.app_context():
        bid = db.session.scalar(db.select(Branch.id).where(Branch.code == "DHA-1"))
    assert post(c, f"/admin/branches/{bid}/edit", {"code": "DHA-1", "name": "DHA Phase 1"}).status_code == 302
    assert post(c, f"/admin/branches/{bid}/status", {"active": "0"}).status_code == 302
    with app.app_context():
        assert db.session.get(Branch, bid).is_active is False and db.session.get(Branch, bid).name == "DHA Phase 1"
    assert {"branch.create", "branch.update", "branch.deactivate"} <= set(audit_actions(app))


def test_last_active_branch_cannot_be_deactivated(admin):
    app, ids, _, c = admin
    assert post(c, f"/admin/branches/{ids['A']}/status", {"active": "0"}).status_code == 302
    assert post(c, f"/admin/branches/{ids['B']}/status", {"active": "0"}).status_code == 422
    with app.app_context():
        assert db.session.get(Branch, ids["B"]).is_active is True


def test_creator_keeps_access_to_a_branch_they_create(env):
    app, ids = env
    from tests.helpers import make_role
    make_role(app, "Branch Creator", ["branches.manage", "dashboard.view"])
    make_user(app, "bc", "Branch Creator", branches=[ids["A"]])
    c = login(app, "bc")
    assert post(c, "/admin/branches/new", {"code": "NEW", "name": "Fresh"}).status_code == 302
    codes = [b["code"] for b in c.get("/api/v1/me").get_json()["data"]["branches"]]
    assert sorted(codes) == ["A", "NEW"]
    assert c.get(f"/admin/branches/{ids['B']}/edit").status_code == 404  # others stay hidden


def test_health_reports_database_state(db_client):
    r = db_client.get("/api/v1/health")
    assert r.get_json()["data"] == {"status": "ok", "database": "ok", "version": "0.1.0"}
