"""Expenses and their categories. A cash expense debits the open cash register, same as any
other cash movement, so the till's expected balance always reflects money spent from it."""
from __future__ import annotations

from app.core.authz import allowed_branch_ids
from app.core.dates import as_date
from app.core.errors import NotFoundError, ValidationError
from app.core.money import D, money
from app.extensions import db
from app.models.expenses import EXPENSE_PAYMENT_METHODS, Expense, ExpenseCategory
from app.services import audit


def list_categories(include_inactive=True):
    stmt = db.select(ExpenseCategory).order_by(ExpenseCategory.name)
    if not include_inactive:
        stmt = stmt.where(ExpenseCategory.is_active.is_(True))
    return list(db.session.scalars(stmt))


def get_category_or_404(category_id: int) -> ExpenseCategory:
    c = db.session.get(ExpenseCategory, category_id)
    if c is None:
        raise NotFoundError("Expense category not found.")
    return c


def save_category(actor, data: dict, existing: ExpenseCategory | None = None) -> ExpenseCategory:
    name = (data.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValidationError("Enter a category name.", details={"name": "Required, up to 80 characters."})
    clash = db.session.scalar(db.select(ExpenseCategory)
                              .where(db.func.lower(ExpenseCategory.name) == name.lower()))
    if clash and (existing is None or clash.id != existing.id):
        raise ValidationError("Please fix the highlighted fields.",
                              details={"name": "Another category already has this name."})
    desc = (data.get("description") or "").strip()[:255] or None
    if existing is None:
        c = ExpenseCategory(name=name, description=desc)
        db.session.add(c)
        db.session.flush()
        audit.log("expense_category.create", "expenses", record_type="expense_category",
                 record_id=c.id, after={"name": name})
        return c
    existing.name, existing.description = name, desc
    audit.log("expense_category.update", "expenses", record_type="expense_category",
             record_id=existing.id, after={"name": name})
    return existing


def set_category_active(actor, c: ExpenseCategory, active: bool) -> None:
    if c.is_active != active:
        c.is_active = active
        audit.log("expense_category.activate" if active else "expense_category.deactivate",
                 "expenses", record_type="expense_category", record_id=c.id)


def visible_stmt(actor, *, branch_id=None, category_id=None, date_from=None, date_to=None):
    date_from, date_to = as_date(date_from), as_date(date_to)
    stmt = db.select(Expense).order_by(Expense.expense_date.desc(), Expense.id.desc())
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Expense.branch_id.in_(ids))
    if branch_id:
        stmt = stmt.where(Expense.branch_id == branch_id)
    if category_id:
        stmt = stmt.where(Expense.category_id == category_id)
    if date_from:
        stmt = stmt.where(Expense.expense_date >= date_from)
    if date_to:
        stmt = stmt.where(Expense.expense_date <= date_to)
    return stmt


def get_or_404(actor, expense_id: int) -> Expense:
    ids = allowed_branch_ids(actor)
    e = db.session.get(Expense, expense_id)
    if e is None or (ids is not None and e.branch_id not in ids):
        raise NotFoundError("Expense not found.")
    return e


def _any_location_for_branch(branch_id):
    from app.models.inventory import Location
    loc = db.session.scalar(db.select(Location).where(Location.branch_id == branch_id,
                                                       Location.is_active.is_(True)).order_by(Location.id))
    return loc.id if loc else None


def create(actor, data: dict) -> Expense:
    from app.core.authz import ensure_branch_access
    from app.models.auth import Branch
    from app.services import cash as cash_svc

    errors = {}
    branch_id = int(data.get("branch_id")) if str(data.get("branch_id") or "").isdigit() else None
    if branch_id is None or db.session.get(Branch, branch_id) is None:
        errors["branch_id"] = "Choose a branch."
    elif allowed_branch_ids(actor) is not None:
        try:
            ensure_branch_access(branch_id, actor)
        except Exception:  # noqa: BLE001
            errors["branch_id"] = "You do not have access to that branch."
    category = db.session.get(ExpenseCategory, int(data["category_id"])) \
        if str(data.get("category_id") or "").isdigit() else None
    if category is None or not category.is_active:
        errors["category_id"] = "Choose an active category."
    try:
        amount = money(data.get("amount"))
        assert amount > 0
    except Exception:  # noqa: BLE001
        errors["amount"] = "Enter an amount greater than zero."
        amount = D("0.00")
    method = data.get("method") or "cash"
    if method not in EXPENSE_PAYMENT_METHODS:
        errors["method"] = "Choose a valid payment method."
    expense_date = as_date(data.get("expense_date"))
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    # Cash expenses debit whichever cash register is open at this branch, same as cash refunds.
    # With several locations/sessions open at once this picks one arbitrarily (most restaurants
    # run a single drawer); a location-specific choice is a reasonable future refinement.
    session = None
    if method == "cash":
        loc_id = _any_location_for_branch(branch_id)
        session = cash_svc.open_session_for(loc_id) if loc_id else None
    e = Expense(branch_id=branch_id, category_id=category.id, amount=amount, method=method,
               expense_date=expense_date, description=(data.get("description") or "").strip() or None,
               reference=(data.get("reference") or "").strip()[:120] or None,
               cash_session_id=session.id if session else None,
               recorded_by=actor.id if actor else None)
    db.session.add(e)
    db.session.flush()
    if method == "cash" and session:
        cash_svc.cash_out(actor, session, amount, f"Expense: {category.name}")
    audit.log("expense.create", "expenses", record_type="expense", record_id=e.id, branch_id=branch_id,
             after={"category": category.name, "amount": str(amount)})
    return e


def total_for_period(branch_id, date_from, date_to):
    return money(db.session.scalar(
        db.select(db.func.coalesce(db.func.sum(Expense.amount), 0))
        .where(Expense.branch_id == branch_id, Expense.expense_date >= date_from,
              Expense.expense_date <= date_to)
    ) or 0)


def by_category_for_period(actor, branch_id, date_from, date_to):
    stmt = (db.select(ExpenseCategory.name, db.func.sum(Expense.amount))
           .join(Expense, Expense.category_id == ExpenseCategory.id)
           .where(Expense.expense_date >= date_from, Expense.expense_date <= date_to)
           .group_by(ExpenseCategory.name).order_by(ExpenseCategory.name))
    ids = allowed_branch_ids(actor)
    if branch_id:
        stmt = stmt.where(Expense.branch_id == branch_id)
    elif ids is not None:
        stmt = stmt.where(Expense.branch_id.in_(ids))
    return list(db.session.execute(stmt).all())
