from datetime import date, timedelta
from types import SimpleNamespace

from app.extensions import db
from app.models.auth import Permission, Role, User
from tests.helpers import PASSWORD, login, make_user


def test_seed_command_is_idempotent(clean):
    runner = clean.test_cli_runner()
    assert runner.invoke(args=["seed"]).exit_code == 0
    assert runner.invoke(args=["seed"]).exit_code == 0
    with clean.app_context():
        assert db.session.scalar(db.select(db.func.count(Role.id))) == 8
        assert db.session.scalar(db.select(db.func.count(Permission.code))) > 60


def test_create_superadmin_command_for_account_recovery(clean):
    runner = clean.test_cli_runner()
    ok = runner.invoke(args=["create-superadmin"], input=f"Rescue\nRescue Admin\n{PASSWORD}\n{PASSWORD}\n")
    assert ok.exit_code == 0, ok.output
    with clean.app_context():
        u = db.session.scalar(db.select(User).where(User.username == "rescue"))
        assert u.role.is_super and u.all_branches and u.password_hash.startswith("$argon2id$")
    assert login(clean, "rescue").get("/api/v1/me").status_code == 200
    dup = runner.invoke(args=["create-superadmin"], input=f"rescue\nX\n{PASSWORD}\n{PASSWORD}\n")
    assert dup.exit_code != 0 and "already exists" in dup.output


def test_audit_date_filters(env):
    app, _ = env
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")  # creates a 'login' audit entry stamped now
    today, tomorrow = date.today(), date.today() + timedelta(days=2)
    assert "login" in c.get(f"/admin/audit?from={today - timedelta(days=1)}&to={tomorrow}").get_data(as_text=True)
    future = c.get(f"/admin/audit?from={tomorrow}").get_data(as_text=True)
    assert "No entries match" in future
    assert c.get("/admin/audit?from=not-a-date").status_code == 200  # ignored, not an error


def test_health_degrades_instead_of_crashing(fresh_client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("app.api.health.db", SimpleNamespace(session=SimpleNamespace(execute=boom)))
    _, client = fresh_client
    r = client.get("/api/v1/health")
    assert r.status_code == 503 and r.get_json()["data"] == {
        "status": "degraded", "database": "unavailable", "version": "0.1.0"}
