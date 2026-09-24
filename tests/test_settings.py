from app.extensions import db
from app.models.audit import AuditLog
from app.models.settings import Setting
from app.services import settings as svc
from tests.helpers import login, make_user, post


def test_defaults_come_from_the_registry(env):
    app, _ = env
    with app.app_context():
        assert svc.get("security.max_failed_logins") == 5 and svc.get("currency.code") == "USD"


def test_update_validates_saves_and_audits(env):
    app, _ = env
    make_user(app, "root", "Super Admin", all_branches=True)
    c = login(app, "root")
    good = {"restaurant.name": "Basil & Co", "locale.timezone": "Asia/Karachi", "currency.code": "pkr",
            "currency.symbol": "Rs", "currency.symbol_position": "before", "security.session_idle_minutes": "60",
            "security.max_failed_logins": "4", "security.lockout_minutes": "10",
            "security.password_min_length": "10", "receipt.show_tax_id": "on",
            "inventory.low_stock_warning_pct": "120", "inventory.expiry_warning_days": "7", "purchasing.default_payment_terms_days": "0"}
    assert post(c, "/admin/settings", good).status_code == 302
    with app.app_context():
        assert svc.get("currency.code") == "PKR" and svc.get("security.session_idle_minutes") == 60
        assert svc.get("receipt.show_tax_id") is True
        e = db.session.scalar(db.select(AuditLog).where(AuditLog.action == "settings.update"))
        assert e.before["currency.code"] == "USD" and e.after["currency.code"] == "PKR"
    assert "Basil &amp; Co" in c.get("/").get_data(as_text=True)  # name shows in the shell, escaped


def test_invalid_settings_change_nothing(env):
    app, _ = env
    make_user(app, "root", "Super Admin", all_branches=True)
    c = login(app, "root")
    bad = {"restaurant.name": "New Name", "locale.timezone": "Mars/Base", "currency.code": "DOLLARS",
           "currency.symbol": "$", "security.session_idle_minutes": "2", "security.max_failed_logins": "abc",
           "security.lockout_minutes": "10", "security.password_min_length": "8",
           "inventory.low_stock_warning_pct": "120", "inventory.expiry_warning_days": "7", "purchasing.default_payment_terms_days": "0"}
    r = post(c, "/admin/settings", bad)
    html = r.get_data(as_text=True)
    assert r.status_code == 422 and "Unknown time zone" in html and "three-letter" in html
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Setting.key))) == 0  # all-or-nothing


def test_settings_actually_drive_behaviour(env):
    app, ids = env
    make_user(app, "root", "Super Admin", all_branches=True)
    make_user(app, "w", "Waiter", branches=[ids["A"]])
    c = login(app, "root")
    base = {"restaurant.name": "R", "locale.timezone": "UTC", "currency.code": "USD", "currency.symbol": "$",
            "currency.symbol_position": "before", "security.session_idle_minutes": "480",
            "security.max_failed_logins": "3", "security.lockout_minutes": "10", "security.password_min_length": "8",
            "inventory.low_stock_warning_pct": "120", "inventory.expiry_warning_days": "7", "purchasing.default_payment_terms_days": "0"}
    post(c, "/admin/settings", base)
    anon = app.test_client()
    from tests.helpers import csrf
    for _ in range(3):  # lockout threshold is now 3, not the default 5
        anon.post("/login", data={"username": "w", "password": "x", "csrf_token": csrf(anon)})
    r = anon.post("/login", data={"username": "w", "password": "Str0ng-pass!", "csrf_token": csrf(anon)})
    assert r.status_code == 401


def test_read_only_view_for_users_without_manage(env):
    app, ids = env
    from tests.helpers import make_role
    make_role(app, "Viewer", ["settings.view"])
    make_user(app, "v", "Viewer", branches=[ids["A"]])
    c = login(app, "v")
    html = c.get("/admin/settings").get_data(as_text=True)
    assert "Save settings" not in html and "disabled" in html
    assert post(c, "/admin/settings", {"restaurant.name": "x"}).status_code == 403
