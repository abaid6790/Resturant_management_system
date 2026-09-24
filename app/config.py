"""Environment-driven configuration. No secrets are hardcoded."""
from __future__ import annotations

import os
import secrets

VALID_ENVS = {"development", "testing", "production"}
DEFAULT_DB_URL = "postgresql+psycopg://erp:erp@localhost:5432/restaurant_erp"


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def load_config(env: str | None = None) -> dict:
    env = (env or os.getenv("APP_ENV") or "development").lower()
    if env not in VALID_ENVS:
        raise ValueError(f"APP_ENV must be one of {sorted(VALID_ENVS)}, got {env!r}")

    secret = os.getenv("SECRET_KEY") or None
    if env == "production" and not secret:
        raise RuntimeError("SECRET_KEY must be set in production.")

    if env == "testing":
        db_url = os.getenv("TEST_DATABASE_URL") or DEFAULT_DB_URL
    else:
        db_url = os.getenv("DATABASE_URL") or DEFAULT_DB_URL

    return {
        "APP_ENV": env,
        "TESTING": env == "testing",
        "DEBUG": env == "development",
        # Random per-process key in dev/test: sessions reset on restart, which is fine there.
        "SECRET_KEY": secret or secrets.token_hex(32),
        "SQLALCHEMY_DATABASE_URI": db_url,
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True},
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "MAX_CONTENT_LENGTH": 10 * 1024 * 1024,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": _bool("SESSION_COOKIE_SECURE", False),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO").upper(),
        # Argon2id cost: light in tests so the suite stays fast, full-strength otherwise.
        "ARGON2_TIME_COST": 1 if env == "testing" else 3,
        "ARGON2_MEMORY_KIB": 8192 if env == "testing" else 65536,
        "ARGON2_PARALLELISM": 1 if env == "testing" else 4,
        "LOGIN_RATE_LIMIT": 20,  # attempts per IP per window
        "LOGIN_RATE_WINDOW": 300,  # seconds
        "SESSION_ABSOLUTE_HOURS": 24,
        "DEFAULT_PAGE_SIZE": 25,
        "MAX_PAGE_SIZE": 200,
    }
