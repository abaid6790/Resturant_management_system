from decimal import Decimal

import pytest

from app.core.authz import public
from app.core.errors import BusinessRuleError, ConflictError
from app.core.http import paginate
from app.extensions import db
from app.models import SchemaMeta


def test_error_envelope_and_request_id(fresh_client):
    app, client = fresh_client

    @app.get("/api/v1/_t/rule")
    @public
    def _rule():
        raise BusinessRuleError("Refund exceeds paid amount", details={"max": "10.00"})

    r = client.get("/api/v1/_t/rule", headers={"X-Request-ID": "abc123"})
    assert r.status_code == 422
    body = r.get_json()
    assert body["error"]["code"] == "business_rule_violation"
    assert body["error"]["details"] == {"max": "10.00"}
    assert body["request_id"] == "abc123" and r.headers["X-Request-ID"] == "abc123"


def test_unhandled_error_does_not_leak(fresh_client):
    app, client = fresh_client

    @app.get("/api/v1/_t/boom")
    @public
    def _boom():
        raise RuntimeError("secret internals")

    r = client.get("/api/v1/_t/boom")
    assert r.status_code == 500
    assert "secret" not in r.get_data(as_text=True)


def test_404_uses_envelope(fresh_client):
    _, client = fresh_client
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.get_json()["error"]["code"] == "not_found"


def test_html_errors_render_a_page(fresh_client):
    _, client = fresh_client
    r = client.get("/nope")
    assert r.status_code == 404 and "text/html" in r.content_type and "Page not found" in r.get_data(as_text=True)


def test_security_headers(fresh_client):
    _, client = fresh_client
    r = client.get("/nope")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_health_ok(db_client):
    r = db_client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.get_json()["data"]["database"] == "ok"


def test_migrated_schema_has_naming_convention(db_app):
    with db_app.app_context():
        pk = db.session.execute(db.text(
            "select conname from pg_constraint "
            "where conrelid='schema_meta'::regclass and contype='p'"
        )).scalar()
        assert pk == "pk_schema_meta"


def test_pagination_server_side_and_validation(app, db_client):
    for i in range(7):
        db.session.add(SchemaMeta(key=f"k{i}", value="v"))
    db.session.commit()
    with app.test_request_context("/?page=2&per_page=3"):
        items, meta = paginate(db.select(SchemaMeta).order_by(SchemaMeta.key))
        assert [i.key for i in items] == ["k3", "k4", "k5"]
        assert meta == {"page": 2, "per_page": 3, "total": 7, "pages": 3}
    from app.core.errors import ValidationError
    for q in ("page=0", "per_page=9999", "page=x"):
        with app.test_request_context(f"/?{q}"), pytest.raises(ValidationError):
            paginate(db.select(SchemaMeta))


def test_numeric_columns_keep_exact_decimals(db_client):
    db.session.execute(db.text("create temp table _t (m numeric(14,2), c numeric(18,6))"))
    db.session.execute(db.text("insert into _t values (0.10+0.20, 0.0085)"))
    m, c = db.session.execute(db.text("select m, c from _t")).one()
    assert m == Decimal("0.30") and c == Decimal("0.008500")


def test_conflict_error_status():
    assert ConflictError.status_code == 409
