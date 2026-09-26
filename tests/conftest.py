import os

import pytest
from flask_migrate import upgrade

from app import create_app
from app.extensions import db


@pytest.fixture(scope="session")
def app():
    return create_app("testing")


@pytest.fixture(scope="session")
def db_app(app):
    """App with the real Alembic migrations applied (skips if no test database configured)."""
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set")
    with app.app_context():
        db.drop_all()
        db.session.execute(db.text("DROP TABLE IF EXISTS alembic_version"))
        db.session.commit()
        upgrade()  # tests run against the migrations, not create_all()
    return app


@pytest.fixture()
def clean(db_app):
    """Empty every table before a test (TRUNCATE is not blocked by the audit trigger)."""
    with db_app.app_context():
        names = ", ".join(t.name for t in db.metadata.sorted_tables)
        db.session.execute(db.text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        db.session.commit()
    db_app.extensions.pop("setup_done", None)
    db_app.extensions["login_limiter"].clear()
    return db_app


@pytest.fixture()
def db_client(clean):
    with clean.app_context():
        yield clean.test_client()


@pytest.fixture()
def env(clean):
    """Seeded roles, units, plus two branches. Returns (app, {'A': id, 'B': id})."""
    from tests.helpers import seed
    return clean, seed(clean)


@pytest.fixture()
def inv(env):
    """env plus a default location per branch and a couple of items. Returns (app, ids, helpers)."""
    from tests.inventory_helpers import setup_inventory
    app, ids = env
    return app, ids, setup_inventory(app, ids)


@pytest.fixture()
def purch(inv):
    """inv plus one supplier. Returns (app, ids, x, supplier_id)."""
    from tests.inventory_helpers import purchasing_setup
    app, ids, x = inv
    return app, ids, x, purchasing_setup(app, ids)


@pytest.fixture()
def pos(inv):
    """inv plus a table and a priced, recipe-backed burger. Returns (app, ids, x, p)."""
    from tests.inventory_helpers import pos_setup
    app, ids, x = inv
    return app, ids, x, pos_setup(app, ids, x)


@pytest.fixture()
def fresh_client():
    """Brand-new app that never touches the database (setup treated as done)."""
    app = create_app("testing")
    app.extensions["setup_done"] = True
    return app, app.test_client()
