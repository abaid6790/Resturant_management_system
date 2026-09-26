# Restaurant ERP (POS + Inventory + Finance)

Desktop application: **Python / Flask / PostgreSQL**, wrapped in a PyWebView window.
Design rules and decisions: [docs/DECISIONS.md](docs/DECISIONS.md).

## Status
| Phase | Scope | State |
|---|---|---|
| 0 | Foundation: config, money/quantity core, errors, migrations, CI, desktop shell | **Done** |
| 1 | Auth, users, roles/permissions, branches, settings, audit framework, UI shell | **Done** |
| 2 | Catalog, units, inventory ledger, batches/FIFO, recipes | **Done** |
| 3 | Suppliers and purchasing | **Done** |
| 4 | POS core: orders, payments, invoices, cash register, `complete_sale` | **Done** |
| 5 | Kitchen (KOT/KDS) | Next |
| 6 | Splits/merges, returns/refunds, credit, loyalty, reservations | |
| 7 | Expenses, employees, attendance, P&L | |
| 8 | Dashboard and reports | |
| 9 | QR menu / ordering | |
| 10 | Backup/restore, hardening, packaging | |

Requires Python 3.11+ and PostgreSQL 14+.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # then set SECRET_KEY
docker compose up -d db                                # or use a native PostgreSQL install
# create the test DB once: createdb -U erp restaurant_erp_test
export FLASK_APP=wsgi:app
flask db upgrade                                       # apply migrations
flask seed                                             # permissions + system roles (setup also does this)
flask run                                              # dev server (production: see below)
```
Open http://127.0.0.1:5000 — on a fresh database you are taken to a one-time **setup page** that creates
the restaurant, first branch and Super Admin. Lost access? `flask create-superadmin`.

Run the desktop window: `pip install -r requirements-desktop.txt && python -m desktop.main`
Attach a second terminal to a server: `python -m desktop.main --server-url http://SERVER:8000`
Production server: `waitress-serve --call wsgi:app`

## Tests and lint
```bash
export TEST_DATABASE_URL=postgresql+psycopg://erp:erp@localhost:5432/restaurant_erp_test
pytest --cov=app
ruff check .
```
Tests that need a database skip automatically if `TEST_DATABASE_URL` is unset.

## Project layout
```
app/config.py        environment config (no hardcoded secrets)
app/extensions.py    db + migrate, constraint naming convention
app/core/money.py    Decimal money/qty/cost, rounding, allocate(), cash_round()
app/core/errors.py   typed errors + JSON error envelope
app/core/http.py     response envelope, server-side pagination
app/models/          SQLAlchemy models (imported in __init__ for Alembic)
app/api/             JSON blueprints under /api/v1 (health, me)
app/web/             server-rendered screens (auth, users, roles, branches, settings, audit)
app/services/        business rules; each change + its audit entry in one transaction
app/services/inventory.py  the FIFO stock ledger: receive/consume/adjust/transfer/wastage
app/services/recipes.py    BOM costing and consumption (recurses through sub-recipes)
app/services/purchasing.py suppliers, PO lifecycle, receiving, invoices, payments, returns
app/services/pos.py        order building, pricing (tax/discount/service charge), complete_sale()
app/services/cash.py       cash register sessions (append-only cash_movements ledger)
app/core/authz.py    @public, @require, branch_scope(), escalation guards
app/core/permissions.py  the permission catalogue for the whole system
migrations/          Alembic migrations (never edit applied ones)
desktop/main.py      PyWebView shell (standalone or client mode)
tests/               pytest suite (runs against real migrations)
```
Every new module follows: model -> migration -> service (transactional posting) -> API -> tests.
