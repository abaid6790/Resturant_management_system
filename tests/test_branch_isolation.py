from app.core.authz import allowed_branch_ids, branch_scope
from app.extensions import db
from app.models.audit import AuditLog
from app.models.auth import Branch, User
from tests.helpers import login, make_role, make_user, post

ADMIN_PERMS = ["users.view", "users.manage", "roles.manage", "audit.view", "branches.manage",
               "dashboard.view", "pos.sell"]


def _world(env):
    app, ids = env
    make_role(app, "Branch Admin", ADMIN_PERMS)
    make_role(app, "Floor Staff", ["pos.sell", "dashboard.view"])  # a role within the admin's own access
    ua = make_user(app, "admin_a", "Branch Admin", branches=[ids["A"]])
    users = {
        "u_a": make_user(app, "u_a", "Floor Staff", branches=[ids["A"]]),
        "u_b": make_user(app, "u_b", "Floor Staff", branches=[ids["B"]]),
        "u_ab": make_user(app, "u_ab", "Floor Staff", branches=[ids["A"], ids["B"]]),
        "u_all": make_user(app, "u_all", "Owner", all_branches=True),
        "root": make_user(app, "root", "Super Admin", all_branches=True),
    }
    return app, ids, ua, users


def test_restricted_admin_only_sees_users_in_own_branches(env):
    app, ids, _, users = _world(env)
    html = login(app, "admin_a").get("/admin/users").get_data(as_text=True)
    assert "u_a" in html and "u_ab" in html
    for hidden in ("u_b", "u_all", "root"):
        assert f"<td>{hidden}</td>" not in html


def test_hidden_users_are_404_for_read_and_write(env):
    app, ids, _, users = _world(env)
    c = login(app, "admin_a")
    for key in ("u_b", "u_all", "root"):
        uid = users[key]
        assert c.get(f"/admin/users/{uid}/edit").status_code == 404
        assert post(c, f"/admin/users/{uid}/status", {"active": "0"}).status_code == 404
        assert post(c, f"/admin/users/{uid}/reset-password", {"password": "Zx9-newpass!"}).status_code == 404


def test_cannot_grant_branches_or_all_access_beyond_own(env):
    app, ids, _, _ = _world(env)
    c = login(app, "admin_a")
    from tests.helpers import make_role as _  # noqa: F401
    with app.app_context():
        from app.models.auth import Role
        rid = db.session.scalar(db.select(Role.id).where(Role.name == "Floor Staff"))
    base = {"username": "new1", "full_name": "New One", "role_id": rid, "password": "Zx9-newpass!"}
    assert post(c, "/admin/users/new", dict(base, branch_ids=[ids["B"]])).status_code == 403
    assert post(c, "/admin/users/new", dict(base, all_branches="on")).status_code == 403
    assert post(c, "/admin/users/new", dict(base, branch_ids=[ids["A"]])).status_code == 302


def test_editing_keeps_branches_outside_admins_scope(env):
    app, ids, _, users = _world(env)
    c = login(app, "admin_a")
    with app.app_context():
        rid = db.session.get(User, users["u_ab"]).role_id
    r = post(c, f"/admin/users/{users['u_ab']}/edit", {
        "full_name": "Renamed", "role_id": rid, "branch_ids": [ids["A"]]})
    assert r.status_code == 302
    with app.app_context():
        assert {b.id for b in db.session.get(User, users["u_ab"]).branches} == {ids["A"], ids["B"]}


def test_branch_switcher_rejects_inaccessible_branch(env):
    app, ids, _, _ = _world(env)
    c = login(app, "admin_a")
    post(c, "/branch/switch", {"branch_id": ids["B"]})
    assert c.get("/api/v1/me").get_json()["data"]["current_branch_id"] == ids["A"]


def test_multi_branch_user_can_switch_and_view_all(env):
    app, ids, _, _ = _world(env)
    make_user(app, "two", "Cashier", branches=[ids["A"], ids["B"]])
    c = login(app, "two")
    post(c, "/branch/switch", {"branch_id": ids["B"]})
    assert c.get("/api/v1/me").get_json()["data"]["current_branch_id"] == ids["B"]
    post(c, "/branch/switch", {"branch_id": "all"})
    assert c.get("/api/v1/me").get_json()["data"]["current_branch_id"] is None


def test_branch_scope_helpers(env):
    app, ids, _, users = _world(env)
    with app.app_context():
        a_only, all_user = db.session.get(User, users["u_a"]), db.session.get(User, users["u_all"])
        assert allowed_branch_ids(a_only) == {ids["A"]}
        assert allowed_branch_ids(all_user) is None
        stmt = branch_scope(db.select(Branch), Branch.id, a_only)
        assert [b.id for b in db.session.scalars(stmt)] == [ids["A"]]
        assert len(db.session.scalars(branch_scope(db.select(Branch), Branch.id, all_user)).all()) == 2


def test_inactive_branch_grants_no_access(env):
    app, ids, _, users = _world(env)
    with app.app_context():
        db.session.get(Branch, ids["A"]).is_active = False
        db.session.commit()
        assert allowed_branch_ids(db.session.get(User, users["u_a"])) == set()


def test_audit_log_is_branch_scoped(env):
    app, ids, _, _ = _world(env)
    with app.app_context():
        db.session.add_all([AuditLog(action="x.a", module="t", branch_id=ids["A"], reference="REF-A"),
                            AuditLog(action="x.b", module="t", branch_id=ids["B"], reference="REF-B")])
        db.session.commit()
    html = login(app, "admin_a").get("/admin/audit").get_data(as_text=True)
    assert "REF-A" in html and "REF-B" not in html
    make_user(app, "owner2", "Owner", all_branches=True)
    assert "REF-B" in login(app, "owner2").get("/admin/audit").get_data(as_text=True)


def test_cannot_take_over_a_user_with_more_access(env):
    """Resetting the password of a stronger account would be privilege escalation."""
    app, ids, _, _ = _world(env)
    strong = make_user(app, "till", "Cashier", branches=[ids["A"]])  # Cashier > Branch Admin's perms
    c = login(app, "admin_a")
    assert "till" in c.get("/admin/users").get_data(as_text=True)  # visible...
    assert post(c, f"/admin/users/{strong}/reset-password", {"password": "Zx9-newpass!"}).status_code == 403
    assert post(c, f"/admin/users/{strong}/status", {"active": "0"}).status_code == 403
    assert post(c, f"/admin/users/{strong}/unlock").status_code == 403
    assert post(c, f"/admin/users/{strong}/edit", {"full_name": "X", "role_id": 1}).status_code == 403
    login(app, "till")  # ...and the account is untouched
