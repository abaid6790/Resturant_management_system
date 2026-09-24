from flask import Flask


def register_blueprints(app: Flask) -> None:
    from .health import bp as health_bp
    from .me import bp as me_bp

    app.register_blueprint(health_bp, url_prefix="/api/v1")
    app.register_blueprint(me_bp, url_prefix="/api/v1")
