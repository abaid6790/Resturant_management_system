from app.extensions import db
from app.models.audit import AuditLog
from app.models.auth import Permission, Role, User
from tests.helpers import audit_actions, login, make_role, make_user, post, user_row


def _role_id(app, name):
    with app.app_context():
        return db.session.scalar(db.select(Role.id).where(Role.name == name))


def test_create_user_hashes_password_and_never_audits_secrets(env):
    app, ids = env
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    r = post(c, "/admin/users/new", {"username": "Maria", "full_name": "Maria Lopez", "email": "m@x.io",
                                     "role_id": _role_id(app, "Waiter"), "branch_ids": [ids["A"]],
                                     "password": "Temp-Pass-77", "must_change_password": "on"})
    assert r.status_code == 302
    u = user_row(app, "maria")  # stored lowercase
    assert u.password_hash.startswith("$argon2id$") and "Temp-Pass-77" not in u.password_hash
    assert u.must_change_password is True
    with app.app_context():
        entry = db.session.scalar(db.select(AuditLog).where(AuditLog.action == "user.create"))
        dump = str(entry.after) + str(entry.before)
        assert "password_hash" not in dump and "argon2" not in dump and "Temp-Pass-77" not in dump
        assert entry.after["username"] == "maria" and entry.username == "boss"


def test_duplicate_username_is_case_insensitive_and_weak_passwords_rejected(env):
    app, ids = env
    make_user(app, "boss", "Owner", all_branches=True)
    make_user(app, "sam", "Waiter", branches=[ids["A"]])
    c = login(app, "boss")
    base = {"full_name": "S", "role_id": _role_id(app, "Waiter"), "branch_ids": [ids["A"]]}
    assert post(c, "/admin/users/new", dict(base, username="SAM", password="Temp-Pass-77")).status_code == 422
    assert post(c, "/admin/users/new", dict(base, username="newbie", password="12345678")).status_code == 422
    assert post(c, "/admin/users/new", dict(base, username="newbie", password="abc")).status_code == 422
    assert post(c, "/admin/users/new", dict(base, username="a b", password="Temp-Pass-77")).status_code == 422


def test_non_super_user_needs_a_branch(env):
    app, _ = env
    make_user(app, "boss", "Owner", all_branches=True)
    r = post(login(app, "boss"), "/admin/users/new", {"username": "nobr", "full_name": "N",
             "role_id": _role_id(app, "Waiter"), "password": "Temp-Pass-77"})
    assert r.status_code == 422 and "at least one branch" in r.get_data(as_text=True)


def test_cannot_assign_a_role_beyond_your_own_access(env):
    app, ids = env
    make_role(app, "User Admin", ["users.view", "users.manage", "pos.sell"])
    make_user(app, "ua", "User Admin", all_branches=True)
    c = login(app, "ua")
    base = {"username": "try1", "full_name": "T", "branch_ids": [ids["A"]], "password": "Temp-Pass-77"}
    assert post(c, "/admin/users/new", dict(base, role_id=_role_id(app, "Owner"))).status_code == 403
    assert post(c, "/admin/users/new", dict(base, role_id=_role_id(app, "Super Admin"))).status_code == 403
    assert post(c, "/admin/users/new", dict(base, role_id=_role_id(app, "User Admin"))).status_code == 302


def test_only_a_super_admin_can_manage_a_super_admin(env):
    app, _ = env
    root = make_user(app, "root", "Super Admin", all_branches=True)
    make_user(app, "boss", "Owner", all_branches=True)
    boss, rootc = login(app, "boss"), login(app, "root")
    assert post(boss, f"/admin/users/{root}/status", {"active": "0"}).status_code == 403
    assert post(boss, f"/admin/users/{root}/reset-password", {"password": "Zx9-newpass!"}).status_code == 403
    r = post(boss, f"/admin/users/{root}/edit", {"full_name": "Root", "role_id": _role_id(app, "Owner"),
                                                 "all_branches": "on"})
    assert r.status_code == 403  # an Owner cannot demote or take over a Super Admin
    assert user_row(app, "root").is_active
    assert post(rootc, f"/admin/users/{root}/status", {"active": "0"}).status_code == 422  # not yourself
    second = make_user(app, "root2", "Super Admin", all_branches=True)
    assert post(rootc, f"/admin/users/{second}/status", {"active": "0"}).status_code == 302
    assert post(rootc, f"/admin/users/{root}/edit", {"full_name": "Root", "role_id": _role_id(app, "Owner"),
                                                     "all_branches": "on"}).status_code == 422  # own role


def test_last_active_super_admin_rule_backstop(env):
    """Unreachable through the UI (see above) but enforced in the service as defence in depth."""
    app, _ = env
    from app.services import users as svc
    a = make_user(app, "a", "Super Admin", all_branches=True)
    with app.app_context():
        assert svc._active_supers_excluding(db.session.get(User, a)) == 0
        b = make_user(app, "b", "Super Admin", all_branches=True)
        assert svc._active_supers_excluding(db.session.get(User, a)) == 1 and b


def test_role_and_status_changes_end_sessions(env):
    app, ids = env
    make_user(app, "boss", "Owner", all_branches=True)
    uid = make_user(app, "w", "Waiter", branches=[ids["A"]])
    boss, w = login(app, "boss"), login(app, "w")
    assert w.get("/").status_code == 200
    post(boss, f"/admin/users/{uid}/edit", {"full_name": "W", "role_id": _role_id(app, "Cashier"),
                                            "branch_ids": [ids["A"]]})
    assert w.get("/").status_code == 302  # must sign in again so new access is evaluated
    w = login(app, "w")
    post(boss, f"/admin/users/{uid}/status", {"active": "0"})
    assert w.get("/").status_code == 302


def test_reset_password_forces_change_and_unlocks(env):
    app, ids = env
    make_user(app, "boss", "Owner", all_branches=True)
    make_user(app, "w", "Waiter", branches=[ids["A"]])
    uid = user_row(app, "w").id
    assert post(login(app, "boss"), f"/admin/users/{uid}/reset-password", {"password": "Fresh-Pass-12"}).status_code == 302
    c = login(app, "w", "Fresh-Pass-12")
    assert c.get("/").headers["Location"].endswith("/account/password")
    assert "password.reset" in audit_actions(app)


def test_role_escalation_is_blocked(env):
    app, _ = env
    make_role(app, "Role Admin", ["roles.manage", "pos.sell"])
    make_user(app, "ra", "Role Admin", all_branches=True)
    c = login(app, "ra")
    assert post(c, "/admin/roles/new", {"name": "Sneaky", "permissions": ["pos.sell", "pos.void"]}).status_code == 403
    assert post(c, "/admin/roles/new", {"name": "Fine", "permissions": ["pos.sell"]}).status_code == 302
    rid = _role_id(app, "Cashier")  # has permissions beyond Role Admin -> cannot even edit it
    assert post(c, f"/admin/roles/{rid}/edit", {"name": "Cashier", "permissions": ["pos.sell"]}).status_code == 403
    ra = _role_id(app, "Role Admin")  # ...and cannot grant themselves more
    assert post(c, f"/admin/roles/{ra}/edit", {"name": "Role Admin",
                "permissions": ["roles.manage", "pos.sell", "users.manage"]}).status_code == 403


def test_super_admin_role_is_immutable_and_roles_in_use_cannot_be_deleted(env):
    app, ids = env
    make_user(app, "root", "Super Admin", all_branches=True)
    make_user(app, "w", "Waiter", branches=[ids["A"]])
    c = login(app, "root")
    assert post(c, f"/admin/roles/{_role_id(app, 'Super Admin')}/edit", {"name": "Super Admin"}).status_code == 422
    assert post(c, f"/admin/roles/{_role_id(app, 'Waiter')}/delete").status_code == 422  # system + in use
    made = make_role(app, "Temp", ["pos.sell"])
    assert post(c, f"/admin/roles/{made}/delete").status_code == 302
    assert "role.delete" in audit_actions(app)


def test_role_permission_change_is_audited_with_before_and_after(env):
    app, _ = env
    make_user(app, "root", "Super Admin", all_branches=True)
    rid = make_role(app, "Custom", ["pos.sell"])
    post(login(app, "root"), f"/admin/roles/{rid}/edit", {"name": "Custom", "permissions": ["pos.sell", "pos.hold"]})
    with app.app_context():
        e = db.session.scalar(db.select(AuditLog).where(AuditLog.action == "role.update"))
        assert e.before == {"permissions": ["pos.sell"]}
        assert e.after == {"permissions": ["pos.hold", "pos.sell"]}


def test_all_catalogue_permissions_are_synced_to_the_database(env):
    app, _ = env
    from app.core.permissions import ALL_CODES
    with app.app_context():
        assert {p.code for p in db.session.scalars(db.select(Permission))} == set(ALL_CODES)
        assert db.session.scalar(db.select(db.func.count(User.id))) == 0
