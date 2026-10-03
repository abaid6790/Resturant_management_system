"""
Customer credit ledger. Same shape as the supplier ledger (Phase 3): an append-only
CustomerLedgerEntry per event with previous/new balance, and a cached CustomerBalance kept in
step with it inside the same transaction. Positive amount = the customer owes more (a sale put
on credit); negative = they owe less (a payment, or a refund that offsets what they owe).
"""
from __future__ import annotations

from app.core.authz import allowed_branch_ids
from app.core.errors import BusinessRuleError, ValidationError
from app.core.money import D, money
from app.extensions import db
from app.models.pos import Customer, CustomerBalance, CustomerLedgerEntry, CustomerPayment
from app.services import audit


def balance_of(customer_id: int):
    row = db.session.get(CustomerBalance, customer_id)
    return row.balance if row else D("0.00")


def _balance_row(customer_id: int) -> CustomerBalance:
    row = db.session.get(CustomerBalance, customer_id)
    if row is None:
        row = CustomerBalance(customer_id=customer_id, balance=D("0.00"))
        db.session.add(row)
        db.session.flush()
    return row


def post(*, customer_id, branch_id, entry_type, amount, reference_type, reference_id, notes,
         actor) -> CustomerLedgerEntry:
    bal = _balance_row(customer_id)
    previous = bal.balance
    new_balance = money(D(previous) + D(amount))
    bal.balance = new_balance
    entry = CustomerLedgerEntry(
        customer_id=customer_id, branch_id=branch_id, entry_type=entry_type, amount=money(amount),
        previous_balance=previous, new_balance=new_balance, reference_type=reference_type,
        reference_id=None if reference_id is None else str(reference_id), notes=notes,
        user_id=actor.id if actor else None,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def ledger_stmt(customer_id: int, actor=None):
    stmt = (db.select(CustomerLedgerEntry).where(CustomerLedgerEntry.customer_id == customer_id)
            .order_by(CustomerLedgerEntry.id.desc()))
    ids = allowed_branch_ids(actor) if actor is not None else None
    if ids is not None:
        stmt = stmt.where(db.or_(CustomerLedgerEntry.branch_id.in_(ids),
                                 CustomerLedgerEntry.branch_id.is_(None)))
    return stmt


def record_payment(actor, customer: Customer, data: dict) -> CustomerPayment:
    """A customer paying down what they owe. Cannot exceed the balance (no negative balances)."""
    errors = {}
    try:
        amount = money(data.get("amount"))
        assert amount > 0
    except Exception:  # noqa: BLE001
        errors["amount"] = "Enter an amount greater than zero."
        amount = D("0.00")
    method = data.get("method") or "cash"
    if method not in ("cash", "card", "bank_transfer", "mobile_wallet", "other"):
        errors["method"] = "Choose a valid payment method."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    owed = balance_of(customer.id)
    if amount > D(owed):
        raise BusinessRuleError(f"{customer.name} only owes {owed}; a payment of {amount} is too much.")

    ids = allowed_branch_ids(actor)
    branch_id = int(data["branch_id"]) if str(data.get("branch_id") or "").isdigit() else (
        next(iter(sorted(ids))) if ids else None)
    if branch_id is None:
        raise ValidationError(
            "Pick a specific branch (using the switcher in the top bar) before recording a "
            "payment — not 'All branches'.", details={"branch_id": "Choose a branch."})
    if ids is not None and branch_id not in ids:
        raise ValidationError("Choose a branch.", details={"branch_id": "You do not have access."})

    if method == "cash":
        from app.models.pos import CashRegisterSession
        from app.services import cash as cash_svc
        session = db.session.scalar(db.select(CashRegisterSession).where(
            CashRegisterSession.branch_id == branch_id, CashRegisterSession.status == "open"))
        if session is not None:
            cash_svc.record_customer_payment_cash(session, amount, customer_name=customer.name,
                                                  actor=actor)

    pay = CustomerPayment(customer_id=customer.id, branch_id=branch_id, amount=amount, method=method,
                          reference=(data.get("reference") or "").strip()[:120] or None,
                          notes=(data.get("notes") or "").strip()[:255] or None,
                          recorded_by=actor.id if actor else None)
    db.session.add(pay)
    db.session.flush()
    post(customer_id=customer.id, branch_id=branch_id, entry_type="payment", amount=-amount,
         reference_type="customer_payment", reference_id=pay.id, notes="Payment on account",
         actor=actor)
    audit.log("customer_payment.create", "customers", record_type="customer_payment",
             record_id=pay.id, branch_id=branch_id, after={"customer": customer.name,
                                                            "amount": str(amount)})
    return pay
