# 🍽️ Restaurant ERP
### POS · Inventory · Kitchen · Purchasing · Finance

A desktop-first **Restaurant Enterprise Resource Planning (ERP)** system built with **Python, Flask, and PostgreSQL**, wrapped in a native desktop window using **PyWebView**.

Designed to manage restaurant operations through a single system — from inventory and purchasing to point of sale, kitchen operations, payments, and financial management.

> **One system. Connected operations. Consistent business records.**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Flask-Backend-000000?style=for-the-badge&logo=flask&logoColor=white" alt="Flask">
  <img src="https://img.shields.io/badge/PostgreSQL-Database-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/PyWebView-Desktop-6C5CE7?style=for-the-badge" alt="PyWebView">
  <img src="https://img.shields.io/badge/Status-In%20Development-orange?style=for-the-badge" alt="In Development">
</p>

---

## 📌 Project Status

Development is organized into 11 phases. Phases 0–6 are complete. Phase 7 is the next development milestone.

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundation, configuration, money/quantity core, errors, migrations, CI, desktop shell | ✅ Complete |
| 1 | Authentication, users, roles, permissions, branches, settings, audit framework, UI shell | ✅ Complete |
| 2 | Catalog, units, inventory ledger, batches, FIFO, recipes | ✅ Complete |
| 3 | Suppliers and purchasing | ✅ Complete |
| 4 | POS core, orders, payments, invoices, cash register, `complete_sale` | ✅ Complete |
| 5 | Kitchen operations, KOT and KDS | ✅ Complete |
| 6 | Splits, merges, returns, refunds, customer credit, loyalty, reservations | ✅ Complete |
| 7 | Expenses, employees, attendance, profit and loss | 🔜 Next |
| 8 | Dashboard and reports | ⏳ Planned |
| 9 | QR menu and online ordering | ⏳ Planned |
| 10 | Backup, restore, hardening, packaging | ⏳ Planned |

**Current development position:** Core restaurant operations are implemented. Financial management, reporting, QR ordering, and final operational hardening remain on the roadmap.

---

## ✨ Implemented Features

### 🔐 Authentication & Access Management
- User authentication and account management
- Role-based access control
- Permission-based authorization
- Branch management and branch-level access scope
- Restaurant and system settings
- Audit framework for tracking business changes
- One-time initial setup for a fresh database

### 📦 Inventory & Recipe Management
- Product and catalog management
- Units of measurement
- Inventory movement ledger
- Batch-based stock tracking
- FIFO inventory costing
- Stock receiving, consumption, adjustments, transfers, and wastage
- Recipe and sub-recipe management
- Bill of Materials (BOM) costing
- Recipe-based inventory consumption

### 🚚 Supplier & Purchasing Management
- Supplier management
- Purchase order lifecycle
- Purchase receiving
- Supplier invoices and payments
- Purchase returns
- Supplier ledger operations

### 🧾 Point of Sale (POS)
- Order creation and management
- Product pricing
- Tax, discount, and service charge calculations
- Sale completion through transactional `complete_sale()`
- Payment processing
- Invoice generation
- Cash register sessions
- Append-only cash movement records

### 👨‍🍳 Kitchen Management
- Kitchen Order Tickets (KOT)
- Kitchen Display System (KDS)
- KOT firing and ticket lifecycle
- Kitchen station grouping
- Live kitchen updates using Server-Sent Events (SSE)

### 💳 Customer & Order Management
- Split and merged orders
- Partial and full refunds
- Refund pricing based on the original sale snapshot
- Customer credit ledger
- Loyalty points earning and redemption
- Loyalty point clawbacks
- Reservation lifecycle and seating management

---

## 🏗️ Architecture

Restaurant ERP follows a layered architecture that separates business rules, database operations, and user interfaces.

<pre>
┌─────────────────────────────────────────────┐
│                 User Interface              │
│                                             │
│          Flask Web UI / PyWebView           │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│                 Application                 │
│                                             │
│        Flask Routes / JSON API              │
│              /api/v1                        │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│              Business Services              │
│                                             │
│  POS │ Inventory │ Recipes │ Purchasing     │
│  Cash │ Kitchen │ Refunds │ Loyalty         │
│  Reservations │ Customer Ledger             │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│               Data & Persistence            │
│                                             │
│      SQLAlchemy Models / PostgreSQL         │
│              Alembic Migrations             │
└─────────────────────────────────────────────┘
</pre>

### Desktop and Server Modes

The application supports two ways of accessing the system:

| Mode | Description |
|---|---|
| Standalone desktop | Opens the Flask application inside a PyWebView desktop window. |
| Client mode | Connects a desktop window to an existing server using a server URL. |
| Web mode | Runs the Flask application and makes its interface accessible through a browser. |

The desktop shell does not replace the Flask backend. It provides a desktop interface to the same application.

---

## 🧠 Important Engineering Decisions

The system is built around consistent financial and inventory records.

- **Decimal-based calculations:** Money, quantities, and costs use `Decimal` rather than floating-point arithmetic.
- **FIFO inventory:** Stock consumption follows batch-based FIFO costing.
- **Transactional business operations:** Business changes and their audit entries are committed within the same transaction.
- **Append-only ledgers:** Cash movements and loyalty records preserve transaction history.
- **Original sale snapshots:** Refunds use the pricing information captured in the original sale.
- **Branch-level authorization:** Access is controlled through permissions and branch scope.
- **Database migrations:** Schema changes are managed through Alembic migrations.
- **Server-side pagination:** API responses support controlled pagination.

See [`docs/DECISIONS.md`](docs/DECISIONS.md) for the complete design decisions and implementation rules.

---

## 🛠️ Technology Stack

| Component | Technology |
|---|---|
| Programming language | Python 3.11+ |
| Backend framework | Flask |
| Database | PostgreSQL 14+ |
| ORM | SQLAlchemy |
| Database migrations | Alembic / Flask-Migrate |
| Desktop wrapper | PyWebView |
| Application server | Waitress |
| Testing | Pytest |
| Code quality | Ruff |
| Test coverage | pytest-cov |
| Live kitchen updates | Server-Sent Events (SSE) |
| Containerized database | Docker Compose |

---

## 🚀 Getting Started

### Prerequisites

Install the following before running the application:

- Python 3.11 or newer
- PostgreSQL 14 or newer
- Git
- Docker Desktop (optional, if using the containerized database)

### 1. Clone the repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd <YOUR_PROJECT_FOLDER>
```

### 2. Create a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements-dev.txt
```

### 4. Configure environment variables

Copy the example environment file:

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

**Linux / macOS:**

```bash
cp .env.example .env
```

Edit `.env` and configure at least the required `SECRET_KEY` and database connection settings.

Use secure, locally generated secrets. Never commit your actual `.env` file or production credentials.

### 5. Start PostgreSQL

**Option A — Docker Compose**

```bash
docker compose up -d db
```

**Option B — Native PostgreSQL**

Start your local PostgreSQL service and ensure the configured database and user exist.

### 6. Create the test database

Create the test database once:

```bash
createdb -U erp restaurant_erp_test
```

This database is used by the test suite.

### 7. Initialize the application

Set the Flask application:

**Windows PowerShell:**

```powershell
$env:FLASK_APP = "wsgi:app"
```

**Linux / macOS:**

```bash
export FLASK_APP=wsgi:app
```

Apply database migrations:

```bash
flask db upgrade
```

Seed permissions and system roles:

```bash
flask seed
```

The initial setup process also handles this seeding.

### 8. Run the application

```bash
flask run
```

Open:

**http://127.0.0.1:5000**

On a fresh database, the application displays a one-time setup page. Use it to create:

1. The restaurant
2. The first branch
3. The Super Admin account

If Super Admin access is lost, use the management command:

```bash
flask create-superadmin
```

---

## 🖥️ Run the Desktop Application

Install the desktop-specific dependencies:

```bash
pip install -r requirements-desktop.txt
```

Launch the desktop application:

```bash
python -m desktop.main
```

### Connect a Desktop Window to a Server

To open a second desktop client connected to an existing application server:

```bash
python -m desktop.main --server-url http://SERVER:8000
```

Replace `SERVER` with the address of the machine running the application.

### Production Server

For production-style serving, use Waitress:

```bash
waitress-serve --call wsgi:app
```

For a production deployment, configure the database, secrets, network access, backups, and other operational requirements appropriately.

---

## 🧪 Testing & Code Quality

The project includes a Pytest suite designed to run against real database migrations.

### Configure the test database

```bash
export TEST_DATABASE_URL=postgresql+psycopg://erp:erp@localhost:5432/restaurant_erp_test
```

On Windows PowerShell:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://erp:erp@localhost:5432/restaurant_erp_test"
```

### Run tests with coverage

```bash
pytest --cov=app
```

### Run Ruff

```bash
ruff check .
```

**Note:** Tests requiring a database skip automatically when `TEST_DATABASE_URL` is not configured.

---

## 📁 Project Structure

```text
restaurant-erp/
│
├── app/
│   ├── config.py
│   ├── extensions.py
│   │
│   ├── core/
│   │   ├── money.py
│   │   ├── errors.py
│   │   ├── http.py
│   │   ├── authz.py
│   │   ├── permissions.py
│   │   └── events.py
│   │
│   ├── models/
│   │
│   ├── api/
│   │   └── JSON blueprints under /api/v1
│   │
│   ├── web/
│   │   └── Server-rendered application screens
│   │
│   └── services/
│       ├── inventory.py
│       ├── recipes.py
│       ├── purchasing.py
│       ├── pos.py
│       ├── cash.py
│       ├── kitchen.py
│       ├── refunds.py
│       ├── customer_ledger.py
│       ├── loyalty.py
│       └── reservations.py
│
├── desktop/
│   └── main.py
│
├── migrations/
│   └── Alembic database migrations
│
├── tests/
│   └── Pytest test suite
│
├── docs/
│   └── DECISIONS.md
│
├── .env.example
├── requirements-dev.txt
├── requirements-desktop.txt
└── wsgi.py
```

### Important Modules

| Module | Responsibility |
|---|---|
| `core/money.py` | Decimal money, quantity, cost, rounding, allocation, and cash rounding |
| `core/errors.py` | Typed application errors and JSON error envelopes |
| `core/http.py` | Response envelopes and server-side pagination |
| `core/authz.py` | Public routes, permission checks, branch scope, and escalation guards |
| `core/permissions.py` | Central permission catalogue |
| `core/events.py` | In-process SSE pub/sub for live kitchen updates |
| `services/inventory.py` | FIFO stock ledger and inventory movements |
| `services/recipes.py` | BOM costing and recursive sub-recipe consumption |
| `services/purchasing.py` | Suppliers, purchase orders, receiving, invoices, payments, and returns |
| `services/pos.py` | Order building, pricing, and sale completion |
| `services/cash.py` | Cash register sessions and cash movement ledger |
| `services/kitchen.py` | KOT firing, ticket lifecycle, and station grouping |
| `services/refunds.py` | Partial/full refunds using original sale snapshots |
| `services/customer_ledger.py` | Customer credit ledger |
| `services/loyalty.py` | Points earning, redemption, and clawback |
| `services/reservations.py` | Reservation lifecycle and seating |
| `desktop/main.py` | PyWebView standalone and client-mode desktop shell |

---

## 🔄 Development Workflow

Every new business module follows a consistent development sequence:

```text
Model
  ↓
Database Migration
  ↓
Business Service
  ↓
API Endpoint
  ↓
Automated Tests
```

This ensures that database changes, business rules, API behavior, and tests are developed together.

**Important migration rule:** Never edit an already-applied migration. Create a new migration for subsequent schema changes.

---

## 🗺️ Roadmap

The next development milestones are:

- [ ] **Phase 7:** Expenses, employees, attendance, and profit/loss
- [ ] **Phase 8:** Dashboard and business reports
- [ ] **Phase 9:** QR menu and ordering
- [ ] **Phase 10:** Backup, restore, security hardening, and packaging

The roadmap will be updated as each phase is implemented and verified.

---

## 📚 Documentation

For deeper technical information, start with:

- [`docs/DECISIONS.md`](docs/DECISIONS.md) — architecture, design rules, and engineering decisions.

Additional setup, architecture, and operational documentation can be added here as the documentation set grows.

---

## 🔒 Security & Data Integrity

The application is designed with a focus on reliable business operations:

- Environment-based configuration instead of hardcoded secrets
- Permission-based access control
- Branch-level authorization
- Transactional business services
- Audit framework
- Append-only financial and loyalty ledgers
- Database migrations for controlled schema changes
- Separate test database configuration

Do not expose the development server directly to the public internet. Production deployments require appropriate network security, secret management, database access controls, and backup procedures.

---

## 📌 Current Limitations

Restaurant ERP is still under active development.

- Employee, attendance, expense, and P&L modules are not yet complete.
- Dashboard and reporting features are still planned.
- QR menu and ordering are not yet implemented.
- Backup, restore, final hardening, and packaging remain planned work.
- Production readiness should not be assumed until the remaining phases are implemented and verified.

---

<p align="center">

**Restaurant ERP**

*Bringing restaurant operations together in one system.*

Built with Python · Flask · PostgreSQL · PyWebView

</p>
