import pytest

from app.config import load_config


def test_production_requires_secret(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        load_config("production")


def test_invalid_env_rejected():
    with pytest.raises(ValueError):
        load_config("staging")


def test_testing_uses_test_db(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://x@h/test_db")
    assert load_config("testing")["SQLALCHEMY_DATABASE_URI"].endswith("/test_db")
