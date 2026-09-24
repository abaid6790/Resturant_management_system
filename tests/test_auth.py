from datetime import UTC, datetime, timedelta

from app.extensions import db
from app.models.auth import User, UserSession
from tests.helpers import PASSWORD, audit_actions, csrf, login, make_user, post

SETUP = {"restaurant_name": "Basil & Co", "timezone": "Asia/Karachi", "currency_code": "pkr",
         "currency_symbol": "Rs", "branch_name": "Main", "branch_code": "main",
         "full_name": "Ada Owner", "username": "Ada", "password": PASSWORD, "password2": PASSWORD}


def test_first_run_setup_flow(clean):
    c = clean.test_client()
    assert c.get("/").headers["Location"].endswith("/setup")
    assert c.get("/api/v1/me").status_code == 409  # JSON clients get a clear error
    assert c.get("/api/v1/health").status_code == 200  # health stays reachable
    bad = dict(SETUP, password2="different")
    assert post(c, "/setup", bad).status_code == 422
    assert post(c, "/setup", SETUP).status_code == 302
    assert c.get("/setup").headers["Location"].endswith("/login")  # cannot run twice
    assert post(c, "/setup", SETUP).status_code == 302  # ...and a second POST just redirects
    with clean.app_context():
        assert db.session.scalar(db.select(db.func.count(User.id))) == 1
    assert "system.setup" in audit_actions(clean)
    me = login(clean, "ada").get("/api/v1/me").get_json()["data"]
    assert me["role"] == "Super Admin" and "users.manage" in me["permissions"]


def test_login_session_and_api(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])
    c = login(app, "cash")
    assert c.get("/").status_code == 200
    data = c.get("/api/v1/me").get_json()["data"]
    assert data["username"] == "cash" and "pos.sell" in data["permissions"]
    assert "login" in audit_actions(app)


def test_failed_logins_are_generic_and_audited(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])
    c = app.test_client()
    msgs = set()
    for user, pw in (("ghost", "x"), ("cash", "wrong")):
        r = c.post("/login", data={"username": user, "password": pw, "csrf_token": csrf(c)})
        assert r.status_code == 401
        msgs.add(r.get_data(as_text=True).split('role="alert">')[1].split("<")[0])
    assert len(msgs) == 1  # unknown user and wrong password look identical
    assert audit_actions(app).count("login.failed") == 2


def test_lockout_after_repeated_failures(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])
    c = app.test_client()
    for _ in range(5):
        c.post("/login", data={"username": "cash", "password": "bad", "csrf_token": csrf(c)})
    r = c.post("/login", data={"username": "cash", "password": PASSWORD, "csrf_token": csrf(c)})
    assert r.status_code == 401  # even the right password is refused while locked
    assert "user.locked" in audit_actions(app)
    with app.app_context():
        u = db.session.scalar(db.select(User).where(User.username == "cash"))
        u.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        db.session.commit()
    login(app, "cash")  # lock expired


def test_login_rate_limit(env, monkeypatch):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])  # setup must be complete or /login redirects
    monkeypatch.setattr(app.extensions["login_limiter"], "limit", 3)
    c = app.test_client()
    codes = [c.post("/login", data={"username": "x", "password": "y", "csrf_token": csrf(c)}).status_code
             for _ in range(5)]
    assert codes[:3] == [401] * 3 and codes[3:] == [429, 429]


def test_csrf_is_enforced(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])
    c = login(app, "cash")
    assert c.post("/logout").status_code == 400
    assert c.post("/logout", data={"csrf_token": "forged"}).status_code == 400
    assert c.post("/logout", headers={"X-CSRF-Token": csrf(c)}).status_code == 302
    anon = app.test_client()
    assert anon.post("/login", data={"username": "a", "password": "b"}).status_code == 400  # login CSRF


def test_default_deny(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])  # setup must be complete
    c = app.test_client()
    r = c.get("/admin/users")
    assert r.status_code == 302 and "/login?next=" in r.headers["Location"]
    r = c.get("/api/v1/me")
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "authentication_required"


def test_logout_revokes_server_side(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])
    c = login(app, "cash")
    with c.session_transaction() as s:
        stolen = dict(s)
    post(c, "/logout")
    replay = app.test_client()
    with replay.session_transaction() as s:
        s.update(stolen)
    assert replay.get("/").status_code == 302  # the copied cookie is worthless
    assert "logout" in audit_actions(app)


def test_idle_timeout(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])
    c = login(app, "cash")
    with app.app_context():
        db.session.execute(db.update(UserSession).values(last_seen_at=datetime.now(UTC) - timedelta(hours=9)))
        db.session.commit()
    assert c.get("/").status_code == 302


def test_deactivated_user_loses_session(env):
    app, _ = env
    uid = make_user(app, "cash", "Cashier", branches=[1])
    c = login(app, "cash")
    with app.app_context():
        db.session.get(User, uid).is_active = False
        db.session.commit()
    assert c.get("/").status_code == 302
    r = app.test_client().post("/login", data={"username": "cash", "password": PASSWORD,
                                               "csrf_token": csrf(app.test_client())})
    assert r.status_code in (400, 401)


def test_forced_password_change_and_session_revocation(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1], must_change=True)
    c1, c2 = login(app, "cash"), login(app, "cash")
    assert c1.get("/").headers["Location"].endswith("/account/password")
    weak = post(c1, "/account/password", {"current": PASSWORD, "new": "short", "confirm": "short"})
    assert weak.status_code == 422
    ok = post(c1, "/account/password", {"current": PASSWORD, "new": "Another-Pass9", "confirm": "Another-Pass9"})
    assert ok.status_code == 302
    assert c1.get("/").status_code == 200  # the changing session survives
    assert c2.get("/").status_code == 302  # every other session was signed out
    login(app, "cash", "Another-Pass9")


def test_open_redirect_blocked(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])
    c = app.test_client()
    r = c.post("/login?next=//evil.example", data={"username": "cash", "password": PASSWORD,
                                                   "csrf_token": csrf(c)})
    assert r.headers["Location"] == "/"
