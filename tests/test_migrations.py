from flask_migrate import downgrade, upgrade

from app.extensions import db


def test_migrations_are_reversible(db_app):
    """Every schema change must be able to go down and back up without error, in either order."""
    with db_app.app_context():
        downgrade(revision="0001")
        tables = set(db.inspect(db.engine).get_table_names())
        assert "users" not in tables and "audit_logs" not in tables and "schema_meta" in tables
        assert not db.session.execute(db.text("select 1 from pg_proc where proname='audit_logs_immutable'")).all()
        upgrade()
        assert {"users", "roles", "branches", "audit_logs", "settings"} <= set(db.inspect(db.engine).get_table_names())


def test_inventory_migrations_are_reversible(db_app):
    with db_app.app_context():
        downgrade(revision="0002")
        tables = set(db.inspect(db.engine).get_table_names())
        assert "stock_movements" not in tables and "recipes" not in tables and "users" in tables
        upgrade()
        tables = set(db.inspect(db.engine).get_table_names())
        assert {"units_of_measure", "inventory_items", "locations", "stock_batches",
               "stock_movements", "recipes", "recipe_lines"} <= tables
        # the append-only trigger (UPDATE/DELETE) still exists after a downgrade/upgrade round-trip
        assert db.session.execute(db.text(
            "select 1 from pg_trigger where tgname = 'trg_stock_movements_immutable'"
        )).first() is not None


def test_models_and_migrations_agree(db_app):
    """Autogenerate must find nothing to change: the models and migrations describe the same schema."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    with db_app.app_context(), db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), db.metadata)
        assert diff == [], f"schema drift: {diff}"


def test_purchasing_migrations_are_reversible(db_app):
    with db_app.app_context():
        downgrade(revision="0003")
        tables = set(db.inspect(db.engine).get_table_names())
        assert "suppliers" not in tables and "purchase_orders" not in tables
        assert "stock_movements" in tables  # earlier phases untouched
        upgrade()
        tables = set(db.inspect(db.engine).get_table_names())
        assert {"suppliers", "purchase_orders", "purchase_order_lines", "purchase_receipts",
               "purchase_invoices", "supplier_payments", "supplier_ledger_entries",
               "purchase_returns"} <= tables
        assert db.session.execute(db.text(
            "select 1 from pg_trigger where tgname = 'trg_supplier_ledger_entries_immutable'"
        )).first() is not None


def test_pos_migrations_are_reversible(db_app):
    with db_app.app_context():
        downgrade(revision="0005")
        tables = set(db.inspect(db.engine).get_table_names())
        assert "orders" not in tables and "cash_movements" not in tables
        assert "suppliers" in tables  # earlier phases untouched
        upgrade()
        tables = set(db.inspect(db.engine).get_table_names())
        assert {"floors", "tables", "customers", "product_modifiers", "orders", "order_lines",
               "invoices", "payments", "cash_register_sessions", "cash_movements"} <= tables
        assert db.session.execute(db.text(
            "select 1 from pg_trigger where tgname = 'trg_cash_movements_immutable'"
        )).first() is not None
        assert db.session.execute(db.text(
            "select 1 from pg_indexes where indexname = 'uq_orders_one_open_per_table'"
        )).first() is not None


def test_kitchen_migrations_are_reversible(db_app):
    with db_app.app_context():
        downgrade(revision="0006")
        tables = set(db.inspect(db.engine).get_table_names())
        assert "kitchen_tickets" not in tables and "kitchen_stations" not in tables
        cols = {c["name"] for c in db.inspect(db.engine).get_columns("inventory_items")}
        assert "station_id" not in cols
        cols = {c["name"] for c in db.inspect(db.engine).get_columns("order_lines")}
        assert "is_fired" not in cols
        assert "orders" in tables  # earlier phases untouched
        upgrade()
        tables = set(db.inspect(db.engine).get_table_names())
        assert {"kitchen_stations", "kitchen_tickets", "kitchen_ticket_lines"} <= tables
        cols = {c["name"] for c in db.inspect(db.engine).get_columns("inventory_items")}
        assert "station_id" in cols
        cols = {c["name"] for c in db.inspect(db.engine).get_columns("order_lines")}
        assert "is_fired" in cols
