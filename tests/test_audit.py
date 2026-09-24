import pytest
from sqlalchemy.exc import DBAPIError

from app.extensions import db
from app.models.audit import AuditLog
from app.services import audit
from tests.helpers import login, make_user


def _add(app, n=3):
    with app.app_context():
        for i in range(n):
            db.session.add(AuditLog(action=f"thing.{i}", module="demo", record_type="thing", record_id=str(i)))
        db.session.commit()


def test_audit_rows_cannot_be_updated_or_deleted(clean):
    _add(clean)
    with clean.app_context():
        for sql in ("UPDATE audit_logs SET action = 'tampered'", "DELETE FROM audit_logs"):
            with pytest.raises(DBAPIError, match="append-only"):
                db.session.execute(db.text(sql))
            db.session.rollback()
        assert db.session.scalar(db.select(db.func.count(AuditLog.id))) == 3
        row = db.session.scalar(db.select(AuditLog))
        row.action = "tampered"  # ORM path is blocked by the same trigger
        with pytest.raises(DBAPIError):
            db.session.commit()
        db.session.rollback()


def test_audit_commits_and_rolls_back_with_the_business_change(clean):
    with clean.app_context():
        audit.log("demo.rolled_back", "demo")
        db.session.rollback()
        audit.log("demo.kept", "demo")
        db.session.commit()
        assert db.session.scalars(db.select(AuditLog.action)).all() == ["demo.kept"]


def test_log_change_records_only_what_changed(clean):
    with clean.app_context():
        assert audit.log_change("x", "demo", {"a": 1, "b": 2}, {"a": 1, "b": 2}) is None
        e = audit.log_change("x", "demo", {"a": 1, "b": 2}, {"a": 1, "b": 3})
        assert e.before == {"b": 2} and e.after == {"b": 3}


def test_secrets_never_reach_the_log(clean):
    with clean.app_context():
        e = audit.log("x", "demo", after={"password_hash": "h", "nested": {"token": "t", "ok": "v"}})
        assert e.after == {"nested": {"ok": "v"}}


def test_audit_page_filters_and_paginates(env):
    app, _ = env
    make_user(app, "boss", "Owner", all_branches=True)
    _add(app, 30)
    c = login(app, "boss")
    page = c.get("/admin/audit?per_page=10").get_data(as_text=True)
    assert "Page 1 of" in page
    only = c.get("/admin/audit?module=demo&action=thing.2&q=2").get_data(as_text=True)
    assert "thing.2" in only and "thing.1<" not in only
    assert "No entries match" in c.get("/admin/audit?user=nobody").get_data(as_text=True)
    assert c.get("/admin/audit?per_page=9999").status_code == 422  # server-side limits hold


def test_security_events_are_recorded(env):
    app, _ = env
    make_user(app, "cash", "Cashier", branches=[1])
    login(app, "cash")
    with app.app_context():
        e = db.session.scalar(db.select(AuditLog).where(AuditLog.action == "login"))
        assert e.username == "cash" and e.request_id and e.record_type == "user"
