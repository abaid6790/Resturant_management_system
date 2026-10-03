"""
Refunds. Every amount is calculated from the ORIGINAL sale's persisted per-line numbers
(OrderLine.final_net_amount / final_tax_amount / cogs_amount, snapshotted by complete_sale), never
recalculated live — otherwise a refund could disagree with the receipt if settings changed since.

Restocking is only offered for items with no recipe: a recipe's ingredients were consumed at sale
time, and reversing a multi-ingredient consumption precisely is out of scope here (see
docs/DECISIONS.md ADR-027); those lines can only be written off. Refund payments must sum to
exactly the refund total (no change-giving on money going out).
"""
from __future__ import annotations

from app.core.authz import allowed_branch_ids
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.money import D, money
from app.core.money import qty as qround
from app.core.money import unit_cost as cround
from app.extensions import db
from app.models.inventory import Recipe
from app.models.pos import Invoice, OrderLine, Refund, RefundLine, RefundPayment
from app.services import audit
from app.services import cash as cash_svc
from app.services import inventory as inv_svc
from app.services import settings as settings_svc

ZERO = D("0.00")


def visible_stmt(actor):
    stmt = db.select(Refund).order_by(Refund.id.desc())
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Refund.branch_id.in_(ids))
    return stmt


def get_or_404(actor, refund_id: int) -> Refund:
    ids = allowed_branch_ids(actor)
    r = db.session.get(Refund, refund_id)
    if r is None or (ids is not None and r.branch_id not in ids):
        raise NotFoundError("Refund not found.")
    return r


def get_invoice_or_404(actor, invoice_id: int) -> Invoice:
    ids = allowed_branch_ids(actor)
    inv = db.session.get(Invoice, invoice_id)
    if inv is None or (ids is not None and inv.branch_id not in ids):
        raise NotFoundError("Invoice not found.")
    return inv


def refundable_lines(invoice: Invoice):
    """[(order_line, outstanding_qty), ...] for lines that still have something to refund."""
    out = []
    for ln in invoice.order.lines:
        outstanding = D(ln.quantity) - D(ln.refunded_quantity)
        if outstanding > 0:
            out.append((ln, outstanding))
    return out


def can_restock(order_line: OrderLine) -> bool:
    if not order_line.item.track_batches:
        return False
    has_recipe = db.session.scalar(
        db.select(Recipe.id).where(Recipe.item_id == order_line.item_id, Recipe.is_active.is_(True))
    )
    return has_recipe is None


def _next_refund_number() -> str:
    prefix = settings_svc.get("returns.refund_number_prefix") or "RF"
    n = (db.session.scalar(db.select(db.func.count(Refund.id))) or 0) + 1
    while db.session.scalar(db.select(Refund.id).where(Refund.refund_number == f"{prefix}-{n:06d}")):
        n += 1
    return f"{prefix}-{n:06d}"


def create_refund(actor, invoice: Invoice, data: dict) -> Refund:
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise ValidationError("A reason is required.", details={"reason": "Required."})

    by_id = {ln.id: ln for ln in invoice.order.lines}
    errors, clean = {}, []
    default_restock = bool(settings_svc.get("returns.restock_by_default"))
    for i, raw in enumerate(data.get("lines") or []):
        line_id = raw.get("order_line_id")
        if not line_id:
            continue
        ln = by_id.get(int(line_id))
        if ln is None:
            errors[f"line{i}"] = "Unknown item."
            continue
        outstanding = D(ln.quantity) - D(ln.refunded_quantity)
        try:
            qty = qround(raw.get("quantity"))
            assert 0 < qty <= outstanding
        except Exception:  # noqa: BLE001
            errors[f"line{i}"] = f"Enter a quantity up to {outstanding} for {ln.item.name}."
            continue
        restock = raw.get("restock")
        restock = default_restock if restock is None else bool(restock)
        if restock and not can_restock(ln):
            errors[f"line{i}"] = f"{ln.item.name} cannot be restocked and must be written off."
            continue
        unit_net = cround(D(ln.final_net_amount) / D(ln.quantity))
        unit_tax = cround(D(ln.final_tax_amount) / D(ln.quantity))
        clean.append({"line": ln, "quantity": qty, "net_amount": money(unit_net * qty),
                      "tax_amount": money(unit_tax * qty), "restock": restock})
    if not clean and not errors:
        errors["lines"] = "Choose at least one item to refund."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    try:
        extras = money(data.get("extras_amount") or "0")
        assert extras >= 0
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("Enter a valid additional amount, or leave it blank.",
                              details={"extras_amount": "Enter an amount of zero or more."}) from exc

    net_amount = money(sum((c["net_amount"] for c in clean), ZERO))
    tax_amount = money(sum((c["tax_amount"] for c in clean), ZERO))
    total_amount = money(net_amount + tax_amount + extras)

    payments = _clean_refund_payments(data.get("payments") or [], total_amount)

    refund = Refund(refund_number=_next_refund_number(), invoice_id=invoice.id,
                    branch_id=invoice.branch_id, reason=reason[:255], net_amount=net_amount,
                    tax_amount=tax_amount, extras_amount=extras, total_amount=total_amount,
                    created_by=actor.id if actor else None)
    db.session.add(refund)
    db.session.flush()

    for c in clean:
        ln = c["line"]
        ln.refunded_quantity = qround(D(ln.refunded_quantity) + c["quantity"])
        restocked_cost = ZERO
        if c["restock"]:
            unit_cost = cround(D(ln.cogs_amount) / D(ln.quantity))
            inv_svc.receive_stock(item=ln.item, location=invoice.order.location, quantity=c["quantity"],
                                  unit_cost=unit_cost, actor=actor, source_type="return",
                                  movement_type="sale_return", reference_type="refund",
                                  reference_id=refund.id, reason=f"Return: {reason}")
            restocked_cost = money(unit_cost * c["quantity"])
        db.session.add(RefundLine(refund_id=refund.id, order_line_id=ln.id, quantity=c["quantity"],
                                  net_amount=c["net_amount"], tax_amount=c["tax_amount"],
                                  restocked=c["restock"], restocked_cost=restocked_cost))

    cash_session = None
    for p in payments:
        if p["method"] == "cash":
            cash_session = cash_svc.open_session_for(invoice.order.location_id)
            if cash_session is None:
                raise BusinessRuleError(
                    "No cash register session is open at this location. Open one before "
                    "refunding cash."
                )
        db.session.add(RefundPayment(refund_id=refund.id, branch_id=invoice.branch_id,
                                     cash_session_id=cash_session.id if p["method"] == "cash" else None,
                                     method=p["method"], amount=p["amount"], reference=p["reference"],
                                     paid_by=actor.id if actor else None))
        if p["method"] == "cash":
            cash_svc.record_refund_cash(cash_session, p["amount"], reference_id=refund.id, actor=actor)

    invoice.refunded_total = money(D(invoice.refunded_total) + total_amount)

    if invoice.order.customer_id:
        from app.services import customer_ledger, loyalty
        outstanding_owed = customer_ledger.balance_of(invoice.order.customer_id)
        if outstanding_owed > 0:
            credit_back = money(min(outstanding_owed, total_amount))
            if credit_back > 0:
                customer_ledger.post(customer_id=invoice.order.customer_id, branch_id=invoice.branch_id,
                                    entry_type="refund", amount=-credit_back, reference_type="refund",
                                    reference_id=refund.id, notes=f"Refund {refund.refund_number}",
                                    actor=actor)
        if loyalty.enabled():
            loyalty.claw_back_for_refund(actor, invoice.order.customer, invoice, refund,
                                        D(invoice.refunded_total))

    audit.log("refund.create", "pos", record_type="refund", record_id=refund.id,
             branch_id=refund.branch_id, reference=refund.refund_number,
             after={"total": str(total_amount), "invoice": invoice.invoice_number})
    return refund


def _clean_refund_payments(raw_payments, total_amount):
    errors, clean = {}, []
    for i, p in enumerate(raw_payments):
        method = p.get("method")
        try:
            amount = money(p.get("amount"))
            assert amount > 0
        except Exception:  # noqa: BLE001
            errors[f"refpay{i}"] = "Enter a valid amount."
            continue
        if method not in ("cash", "card", "bank_transfer", "mobile_wallet", "other"):
            errors[f"refpay{i}"] = "Choose a valid payment method."
            continue
        clean.append({"method": method, "amount": amount,
                      "reference": (p.get("reference") or "").strip()[:120] or None})
    if not clean and not errors:
        errors["payments"] = "Record how the refund was paid out."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    paid = money(sum((p["amount"] for p in clean), ZERO))
    if paid != total_amount:
        raise ValidationError("The refund payments must add up to the refund total.",
                              details={"payments": f"Must total exactly {total_amount}."})
    return clean
