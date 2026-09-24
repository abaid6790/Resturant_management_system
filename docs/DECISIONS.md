# Architecture Decision Records

Status: **Accepted** = built into Phase 0. **Proposed** = default we will use unless you say otherwise; confirmed before the phase that needs it.

## ADR-001 Desktop architecture — Accepted
Flask server + PostgreSQL, with a thin PyWebView desktop window. Standalone mode starts the server on
localhost; client mode (`--server-url`) lets extra terminals (second cashier, kitchen display) attach to one
central server. Packaged with PyInstaller (Phase 10). One codebase serves both.

## ADR-002 Exact numerics — Accepted
PostgreSQL `NUMERIC`, Python `Decimal`; floats are rejected by `app.core.money.D()`.
| Kind | Precision | Constant |
|---|---|---|
| Money (prices, payments, tax, totals) | `NUMERIC(14,2)` | `MONEY_DP=2` |
| Stock quantities | `NUMERIC(14,4)` | `QTY_DP=4` |
| Unit costs (e.g. cost per gram) | `NUMERIC(18,6)` | `COST_DP=6` |
| Percent rates | `NUMERIC(9,4)` | `RATE_DP=4` |

Selling price is never used as inventory cost. Currency code/symbol are settings (Phase 1), but the number of
decimal places stays fixed at 2 for now; zero-decimal currencies would need a deliberate change to `money.py`.

## ADR-003 Rounding — Accepted
`ROUND_HALF_UP`. Each line amount is rounded once at line level; document totals are sums of rounded lines
(receipts always add up). Splits and order-level discount/tax spreading use `allocate()` (largest remainder), so
parts sum to the total exactly. Optional cash rounding is recorded as a separate adjustment line.

## ADR-004 Append-only ledgers and posting services — Accepted
Stock movements, customer/supplier ledgers, payments, cash-register entries and audit log are never edited or
deleted; corrections are reversals/adjustments. Business events (`complete_sale`, `receive_purchase`,
`process_return`, `transfer_stock`...) are single service functions that run in **one DB transaction**.
Documents snapshot price, tax, discount and unit cost at the time of the event.

## ADR-005 API conventions — Accepted
Base path `/api/v1`. Success: `{"data": ..., "meta": {...}}`. Error: `{"error": {"code","message","details"}, "request_id"}`.
Typed errors (`ValidationError`, `BusinessRuleError`, `ConflictError`, `PermissionDeniedError`, ...). Unhandled
errors never leak internals. Server-side pagination only (`page`, `per_page`, capped). Every response carries `X-Request-ID`.

## ADR-006 Migrations — Accepted
Alembic via Flask-Migrate, deterministic constraint names (`pk_/fk_/uq_/ck_/ix_`). Applied migrations are never
edited; tests run against the real migrations, not `create_all()`.

## ADR-007 Authentication and sessions — Accepted (Phase 1)
Argon2id hashing (cost is config; light only under test). Server-side sessions: the cookie holds a random token,
the database stores only its SHA-256, so sessions can be revoked instantly (logout, deactivation, role/branch
change, password change). Idle timeout (setting) plus a 24 h absolute limit. CSRF token required on every
non-GET request, including login (a missing token never matches). Account lockout (settings) plus a per-IP
login limiter; failures are indistinguishable for unknown users, wrong passwords and locked accounts.
Not built yet: cashier PIN quick-login (Phase 4, when the POS needs it).

## ADR-008 Frontend — Accepted, revised (Phase 1)
Server-rendered Jinja templates, one CSS file and a small vanilla-JS file (confirm dialog, toasts, menu). Works
offline inside the desktop window; no CDN. HTMX was proposed but is **not** used yet: plain forms are simpler
and more robust for admin screens. It will be introduced where it earns its place (POS, kitchen display).

## ADR-010 Access control — Accepted (Phase 1)
* **Default deny:** every view requires sign-in unless explicitly marked `@public`.
* **One permission catalogue for the whole system** (`app/core/permissions.py`), so roles are designed once;
  sensitive permissions are flagged. `@require("code")` enforces on the backend; menus merely reflect it.
* **No privilege escalation:** you can only grant permissions you hold, assign roles within your own access,
  and manage users whose access fits inside yours. Only a Super Admin can manage a Super Admin. Users cannot
  change their own role or branches. There is always at least one active Super Admin and one active branch.
* **Branch scoping:** `allowed_branch_ids()` / `branch_scope(stmt, column)` in `app/core/authz.py`. Every
  branch-owned query in later phases MUST use it. Hidden records answer 404, not 403.
* Users and branches are deactivated, never deleted, so history and audit references stay valid.

## ADR-011 Audit — Accepted (Phase 1)
Entries are added to the same DB transaction as the change they describe (commit/rollback together). A database
trigger rejects UPDATE and DELETE on `audit_logs`. Only changed fields are stored; secrets are never recorded.
Limits, stated honestly: a database superuser can still bypass a trigger, so production should run the app as a
non-superuser without TRUNCATE/ALTER rights on that table; a tamper-evident hash chain is planned for Phase 10.

## ADR-009 Costing — Proposed (Phase 2)
FIFO batch layers as the primary method; weighted average as an optional per-branch setting later.

## ADR-011a Settings updates are whole-form, by design — Accepted (Phase 1, note added Phase 3)
`settings.update()` validates every `REGISTRY` entry on a full submission (`keys=None`); a field
missing from the posted data is NOT treated as "leave unchanged" and can fail validation (e.g. a
required int with no value). This is deliberate: `settings.html` always renders an input for every
registered setting, so a real browser submission always includes all of them, and treating "absent"
as "keep the old value" would let a template that forgot to render a new field fail silently instead
of erroring. The cost: adding a new setting to the registry means updating any test that posts a
hand-written, full settings-form dict (`test_settings.py`, and similar dicts in later phases' tests
that reuse the same all-fields contract) to include it. `keys=[...]` remains the way to update a
specific subset without touching the rest (used by first-run setup and by tests).

## ADR-012 Units and packaging — Accepted (Phase 2)
Two layers: a small set of global units (g, kg, mg, ml, l, piece, dozen) convert within their kind
(weight/volume/count) via a fixed factor to that kind's base unit. Item-specific packaging (e.g.
"Box" = 24 of THIS item) lives on the item itself (`ItemPackagingUnit`), because the same word means
different quantities for different items. Stock is always held, and every ledger line always
recorded, in the item's own base stock unit; a recipe line can be written in any compatible global
unit and is converted before costing or consuming.

## ADR-013 Costing method — Accepted (Phase 2)
FIFO at the batch level (oldest `received_at` first) for actual consumption and COGS; a weighted
average of currently on-hand batches for display/theoretical costing (recipe cost, dashboard
figures), since that reads more naturally than "whichever layer happens to be oldest right now."
Items that don't track batches (`track_batches=False`) use a simple balance and the most recent
movement's unit cost — appropriate for low-value items where the batch bookkeeping isn't worth it.
Weighted-average, as an alternative to FIFO for consumption itself, remains a future per-branch
setting if needed (see the Phase 0 open questions), not built now.

## ADR-014 Semi-finished items = sub-recipes, not production orders — Accepted (Phase 2)
A semi-finished item (sauce, dough...) gets its own `Recipe`; a parent recipe can use it as an
ingredient, and `cost_of_recipe()` / `consume_for_output()` recurse through it (with a circular-BOM
guard). This was the open question at the end of Phase 1; the alternative — separate manufacturing/
production-order documents with their own yield and wastage tracking — was not built. Revisit if
kitchens need to batch-produce and hold stock of a semi-finished item independently of any one dish
(e.g. make 5 L of sauce this morning, sell it into several dishes over the week): that needs a
"production" posting distinct from "consume-on-demand," which the current model doesn't capture.

## ADR-015 Negative stock is an app-level rule, not a DB constraint — Accepted (Phase 2)
`stock_movements.new_balance` has no DB-level CHECK enforcing it non-negative (migration 0004 drops
the one migration 0003 first added). Going negative is a deliberate, permission-gated override
(`settings.inventory.allow_negative_stock`, off by default) enforced in `app/services/inventory.py`;
a hard DB constraint can't distinguish "blocked" from "allowed via setting." Batch quantities
(`qty_remaining >= 0` and `<= qty_received`) remain hard DB constraints, since those must never be
violated regardless of settings.

## ADR-016 Purchase approval is a setting, not a fixed workflow — Accepted (Phase 3)
`settings.purchasing.require_approval` (off by default). Off: submitting a draft PO approves it
immediately (the submitter and approver can be the same person). On: submitting moves a PO to
`submitted`, and a separate `purchases.approve` permission is required to move it to `approved`
before it can be received — enforced in the service (`approve_po` checks status), not only hidden
in the UI. This was the open question at the end of Phase 2, resolved as a toggle rather than
picking one workflow for everyone.

## ADR-017 Supplier ledger mirrors the stock ledger pattern — Accepted (Phase 3)
`SupplierLedgerEntry` is append-only (same DB trigger function as `audit_logs` and
`stock_movements`), each entry stores `previous_balance`/`new_balance`, and a cached
`SupplierBalance` is kept in step with it inside the same transaction as the event that changed it
(invoice, payment, return) — same shape as `StockBalance`/`StockMovement`. The balance is company-
wide per supplier, not per-branch (unlike stock, which is inherently location-bound); ledger entries
still carry a `branch_id` so purchasing activity can be filtered by branch in reports.

## ADR-018 A receipt posts through the same stock ledger as everything else — Accepted (Phase 3)
`receive_po()` calls `inventory.receive_stock()` per line — the exact function `stock.adjust` and
opening-stock entry use — so a purchase receipt is exactly as traceable as any other stock
movement (`movement_type="purchase_in"`, `reference_type="purchase_receipt"`) and creates a FIFO
batch the same way. No separate "purchase costing" code path exists to drift out of sync with the
ledger.

## Open questions (needed before the phase noted)
| Question | Needed by |
|---|---|
| Menu prices tax-inclusive or tax-exclusive? Is service charge taxable? (asked twice, awaiting answer) | Phase 4 |
| Returned food: back to stock or written off (per-return choice)? | Phase 6 |
| Operational P&L only, or a full double-entry chart of accounts? | Phase 7 |
| Must the POS work offline (server unreachable)? | Before Phase 4 |
| Printers: thermal ESC/POS, USB or network? Barcode scanners? Cash drawer? | Phase 4 |
| Semi-finished goods: production batches or just recipe sub-components? | Phase 2 |
