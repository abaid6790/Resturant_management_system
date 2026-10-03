"""
Loyalty points. Append-only LoyaltyTransaction ledger plus a cached balance on the customer
(Customer.loyalty_points), same pattern as the money ledgers. Earn rate and redemption value are
plain settings (defaults: 1 point per currency unit, 100 points = 1.00), so a restaurant can
change them without code.
"""
from __future__ import annotations

import math

from app.core.errors import BusinessRuleError, ValidationError
from app.core.money import D, money
from app.extensions import db
from app.models.pos import Customer, LoyaltyTransaction
from app.services import audit
from app.services import settings as settings_svc


def enabled() -> bool:
    return bool(settings_svc.get("loyalty.enabled"))


def points_value(points: int):
    return money(D(points) * D(settings_svc.get("loyalty.redemption_value")))


def points_for_amount(amount) -> int:
    return int(math.floor(D(amount) * D(settings_svc.get("loyalty.earn_rate"))))


def _post(customer: Customer, entry_type: str, points: int, *, branch_id, reference_type,
          reference_id, notes, actor) -> LoyaltyTransaction:
    new_balance = customer.loyalty_points + points
    if new_balance < 0:
        raise BusinessRuleError(f"{customer.name} only has {customer.loyalty_points} points.")
    customer.loyalty_points = new_balance
    tx = LoyaltyTransaction(customer_id=customer.id, branch_id=branch_id, entry_type=entry_type,
                            points=points, balance_after=new_balance, reference_type=reference_type,
                            reference_id=None if reference_id is None else str(reference_id),
                            notes=notes, user_id=actor.id if actor else None)
    db.session.add(tx)
    db.session.flush()
    return tx


def earn(actor, customer: Customer, amount, *, branch_id, invoice) -> int:
    points = points_for_amount(amount)
    if points > 0:
        _post(customer, "earn", points, branch_id=branch_id, reference_type="invoice",
              reference_id=invoice.id, notes=f"Earned on {invoice.invoice_number}", actor=actor)
    return points


def redeem(actor, customer: Customer, points: int, *, branch_id, invoice) -> None:
    _post(customer, "redeem", -points, branch_id=branch_id, reference_type="invoice",
          reference_id=invoice.id, notes=f"Redeemed on {invoice.invoice_number}", actor=actor)


def claw_back_for_refund(actor, customer: Customer, invoice, refund, cumulative_refunded) -> int:
    """
    Take back a proportional share of the points this invoice earned when part of it is refunded.
    Uses the cumulative refunded fraction so several partial refunds add up to exactly the right
    total, never more than the customer currently has, and never more than was earned.
    """
    earned = db.session.scalar(
        db.select(db.func.coalesce(db.func.sum(LoyaltyTransaction.points), 0)).where(
            LoyaltyTransaction.customer_id == customer.id, LoyaltyTransaction.entry_type == "earn",
            LoyaltyTransaction.reference_type == "invoice",
            LoyaltyTransaction.reference_id == str(invoice.id)))
    if not earned or D(invoice.total) <= 0:
        return 0
    already = -(db.session.scalar(
        db.select(db.func.coalesce(db.func.sum(LoyaltyTransaction.points), 0)).where(
            LoyaltyTransaction.customer_id == customer.id, LoyaltyTransaction.entry_type == "adjustment",
            LoyaltyTransaction.reference_type == "invoice_refund",
            LoyaltyTransaction.reference_id == str(invoice.id))) or 0)
    target = int(math.floor(D(earned) * D(cumulative_refunded) / D(invoice.total)))
    claw = min(max(0, target - already), customer.loyalty_points)
    if claw > 0:
        _post(customer, "adjustment", -claw, branch_id=invoice.branch_id,
              reference_type="invoice_refund", reference_id=invoice.id,
              notes=f"Reversed on refund {refund.refund_number}", actor=actor)
    return claw


def adjust(actor, customer: Customer, points, reason: str) -> LoyaltyTransaction:
    """Manual correction by someone with loyalty.manage."""
    try:
        points = int(points)
        assert points != 0
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("Enter a non-zero whole number of points.",
                              details={"points": "Enter a non-zero whole number."}) from exc
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.", details={"reason": "Required."})
    tx = _post(customer, "adjustment", points, branch_id=None, reference_type="manual",
               reference_id=None, notes=reason.strip()[:255], actor=actor)
    audit.log("loyalty.adjust", "customers", record_type="customer", record_id=customer.id,
             after={"points": points, "reason": reason.strip()[:255]})
    return tx


def transactions_stmt(customer_id: int):
    return (db.select(LoyaltyTransaction).where(LoyaltyTransaction.customer_id == customer_id)
            .order_by(LoyaltyTransaction.id.desc()))
