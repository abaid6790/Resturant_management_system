"""Restaurant ERP application factory."""
from __future__ import annotations

import logging
import uuid

from dotenv import load_dotenv
from flask import Flask, Response, g, has_request_context, request

from .config import load_config
from .core.errors import register_error_handlers
from .extensions import db, migrate

__version__ = "0.1.0"

class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(g, "request_id", "-") if has_request_context() else "-"
        return True


def _configure_logging(app: Flask) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(app.config["LOG_LEVEL"])


def _register_request_hooks(app: Flask) -> None:
    @app.before_request
    def _request_id():
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex

    @app.after_request
    def _headers(resp: Response) -> Response:
        resp.headers["X-Request-ID"] = getattr(g, "request_id", "")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp


def create_app(env: str | None = None, overrides: dict | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__)
    app.url_map.strict_slashes = False  # /admin/users and /admin/users/ both work
    app.config.update(load_config(env))
    if overrides:
        app.config.update(overrides)

    _configure_logging(app)
    db.init_app(app)
    migrate.init_app(app, db)
    from . import models  # noqa: F401  (register tables with metadata)

    register_error_handlers(app)
    _register_request_hooks(app)

    from .api import register_blueprints
    from .cli import register_cli
    from .core.authn import init_auth
    from .core.events import Broadcaster
    from .web import register_web

    app.extensions["kitchen_events"] = Broadcaster()
    init_auth(app)
    register_blueprints(app)
    register_web(app)
    register_cli(app)
    return app
