"""
The POS core: building an order, pricing it, and completing the sale.

complete_sale() is the single orchestrator described in the spec's business rules: within one
transaction it deducts inventory (via the recipe, or directly if the item has none), computes
COGS from the actual stock movements it just created, posts the invoice and payments, updates the
cash register, frees the table, and writes an audit record. Everything here runs inside the
caller's transaction (no commits in this module) so a sale is all-or-nothing.

Pricing (see docs/DECISIONS.md ADR-019): prices are entered TAX-EXCLUSIVE; tax is added on top.
Order-level discount and service charge are computed after line-level discounts, and the order
discount is spread across lines proportionally (money.allocate) so per-line tax stays accurate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.authz import allowed_branch_ids
from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.money import D, allocate, money, percent_of
from app.core.money import qty as qround
from app.extensions import db
from app.models.inventory import InventoryItem, Recipe
from app.models.pos import (
    DISCOUNT_KINDS,
    ORDER_TYPES,
    Customer,
    Invoice,
    Order,
    OrderLine,
    OrderLineModifier,
    Payment,
    ProductModifier,
    Table,
)
from app.services import audit
from app.services import cash as cash_svc
from app.services import inventory as inv_svc
from app.services import recipes as recipe_svc
from app.services import settings as settings_svc

ZERO = D("0.00")


# ============================================================================ building orders ===

def visible_orders(actor, status: str | None = None, location_id: int | None = None):
    stmt = db.select(Order).order_by(Order.id.desc())
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Order.branch_id.in_(ids))
    if status:
        stmt = stmt.where(Order.status == status)
    if location_id:
        stmt = stmt.where(Order.location_id == location_id)
    return stmt


def get_order_or_404(actor, order_id: int) -> Order:
    ids = allowed_branch_ids(actor)
    o = db.session.get(Order, order_id)
    if o is None or (ids is not None and o.branch_id not in ids):
        raise NotFoundError("Order not found.")
    return o


def _next_number(prefix_key: str, model) -> str:
    prefix = settings_svc.get(prefix_key) or "N"
    n = (db.session.scalar(db.select(db.func.count(model.id))) or 0) + 1
    col = model.order_number if hasattr(model, "order_number") else model.invoice_number
    while db.session.scalar(db.select(model.id).where(col == f"{prefix}-{n:06d}")):
        n += 1
    return f"{prefix}-{n:06d}"


def start_order(actor, data: dict) -> Order:
    from app.models.inventory import Location
    errors = {}
    order_type = data.get("order_type")
    if order_type not in ORDER_TYPES:
        errors["order_type"] = "Choose an order type."
    location = db.session.get(Location, int(data["location_id"])) \
        if str(data.get("location_id") or "").isdigit() else None
    ids = allowed_branch_ids(actor)
    if location is None or (ids is not None and location.branch_id not in ids):
        errors["location_id"] = "Choose a location."
    table = None
    if order_type == "dine_in":
        table = (db.session.get(Table, int(data["table_id"]))
                if str(data.get("table_id") or "").isdigit() else None)
        loc_branch = location.branch_id if location else None
        if table is None or not table.is_active or table.branch_id != loc_branch:
            errors["table_id"] = "Choose an available table."
        elif table.status != "available":
            errors["table_id"] = f"{table.name} is not available."
    customer_id = None
    if data.get("customer_id"):
        customer = db.session.get(Customer, int(data["customer_id"]))
        if customer is None or not customer.is_active:
            errors["customer_id"] = "Choose a valid customer."
        else:
            customer_id = customer.id
    guest_count = None
    if data.get("guest_count"):
        try:
            guest_count = int(data["guest_count"])
            assert guest_count > 0
        except Exception:  # noqa: BLE001
            errors["guest_count"] = "Enter a whole number of guests."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    order = Order(order_number=_next_number("pos.order_number_prefix", Order), branch_id=location.branch_id,
                 location_id=location.id, order_type=order_type, table_id=table.id if table else None,
                 customer_id=customer_id, guest_count=guest_count, status="open",
                 server_id=actor.id if actor else None, created_by=actor.id if actor else None)
    db.session.add(order)
    db.session.flush()
    if table:
        table.status = "occupied"
    audit.log("order.start", "pos", record_type="order", record_id=order.id, branch_id=order.branch_id,
             reference=order.order_number,
             after={"order_type": order_type, "table": table.name if table else None})
    return order


def _ensure_open(order: Order) -> None:
    if order.status != "open":
        raise BusinessRuleError("This order is no longer open.")


def add_line(actor, order: Order, data: dict) -> OrderLine:
    _ensure_open(order)
    errors = {}
    item = db.session.get(InventoryItem, int(data["item_id"])) if str(data.get("item_id") or "").isdigit() \
        else None
    if item is None or not item.is_active:
        errors["item_id"] = "Choose a valid item."
    elif item.selling_price is None:
        errors["item_id"] = f"{item.name} has no selling price set."
    try:
        qty = qround(data.get("quantity"))
        assert qty > 0
    except Exception:  # noqa: BLE001
        errors["quantity"] = "Enter a quantity greater than zero."
        qty = None
    unit_price = item.selling_price if item else None
    if data.get("unit_price") not in (None, ""):
        if not actor or not actor.has_permission("pos.price_override"):
            raise PermissionDeniedError("You do not have permission to override prices.")
        try:
            unit_price = money(data["unit_price"])
            assert unit_price >= 0
        except Exception:  # noqa: BLE001
            errors["unit_price"] = "Enter a valid price."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    modifiers = []
    for mid in data.get("modifier_ids") or []:
        mod = db.session.get(ProductModifier, int(mid))
        if mod and mod.item_id == item.id and mod.is_active:
            modifiers.append(OrderLineModifier(name=mod.name, price_delta=mod.price_delta))

    line = OrderLine(order_id=order.id, item_id=item.id, quantity=qty, unit_price=unit_price,
                     notes=(data.get("notes") or "").strip()[:255] or None)
    line.modifiers = modifiers
    db.session.add(line)
    db.session.flush()
    audit.log("order.add_line", "pos", record_type="order", record_id=order.id, branch_id=order.branch_id,
             reference=order.order_number, after={"item": item.sku, "quantity": str(qty)})
    return line


def remove_line(actor, order: Order, line: OrderLine) -> None:
    _ensure_open(order)
    if line.order_id != order.id:
        raise NotFoundError("Order line not found.")
    db.session.delete(line)
    audit.log("order.remove_line", "pos", record_type="order", record_id=order.id,
             branch_id=order.branch_id, reference=order.order_number)


def set_customer(actor, order: Order, customer_id) -> None:
    _ensure_open(order)
    if not customer_id:
        order.customer_id = None
        return
    customer = db.session.get(Customer, int(customer_id))
    if customer is None or not customer.is_active:
        raise ValidationError("Choose a valid customer.", details={"customer_id": "Invalid customer."})
    order.customer_id = customer.id


def set_discount(actor, order: Order, kind: str, value, reason: str) -> None:
    _ensure_open(order)
    if kind not in DISCOUNT_KINDS:
        raise ValidationError("Choose a valid discount type.")
    if kind == "none":
        order.discount_kind, order.discount_value, order.discount_reason = "none", ZERO, None
        return
    try:
        value = money(value)
        assert value > 0
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("Enter a discount amount greater than zero.",
                              details={"discount_value": "Enter a positive amount."}) from exc
    if not reason or not reason.strip():
        raise ValidationError("A reason is required for discounts.",
                              details={"discount_reason": "Required."})
    if kind == "percentage" and value > 100:
        raise ValidationError("A percentage discount cannot exceed 100%.",
                              details={"discount_value": "Enter at most 100."})
    limit = D(settings_svc.get("pos.discount_limit_pct"))
    effective_pct = value if kind == "percentage" else (
        (value / max(D("0.01"), _lines_subtotal(order))) * 100 if order.lines else D(0))
    needs_large = effective_pct > limit
    if needs_large and not (actor and actor.has_permission("pos.discount_large")):
        raise PermissionDeniedError(
            f"A discount over {limit}% needs the 'Apply large discounts' permission."
        )
    order.discount_kind, order.discount_value = kind, value
    order.discount_reason = reason.strip()[:255]
    audit.log("order.discount", "pos", record_type="order", record_id=order.id, branch_id=order.branch_id,
             reference=order.order_number, after={"kind": kind, "value": str(value)})


def set_delivery_charge(actor, order: Order, amount) -> None:
    _ensure_open(order)
    try:
        amount = money(amount)
        assert amount >= 0
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("Enter a valid delivery charge.",
                              details={"delivery_charge": "Enter an amount of zero or more."}) from exc
    order.delivery_charge = amount


def void_order(actor, order: Order, reason: str) -> None:
    _ensure_open(order)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.", details={"reason": "Required."})
    order.status, order.void_reason = "void", reason.strip()[:255]
    if order.table_id:
        order.table.status = "available"
    audit.log("order.void", "pos", record_type="order", record_id=order.id, branch_id=order.branch_id,
             reference=order.order_number, after={"reason": order.void_reason})


# ============================================================================ pricing ============

@dataclass
class LinePricing:
    line: OrderLine
    base: object
    line_discount: object
    net_after_line_discount: object
    order_discount_share: object = ZERO
    taxable_base: object = ZERO
    tax: object = ZERO


@dataclass
class OrderTotals:
    lines: list = field(default_factory=list)
    subtotal: object = ZERO
    discount_total: object = ZERO
    tax_total: object = ZERO
    service_charge_total: object = ZERO
    delivery_charge_total: object = ZERO
    total: object = ZERO


def _line_base(line: OrderLine):
    per_unit = D(line.unit_price) + sum((D(m.price_delta) for m in line.modifiers), ZERO)
    return money(per_unit * D(line.quantity))


def _lines_subtotal(order: Order):
    return money(sum((_line_base(ln) for ln in order.lines), ZERO))


def _line_discount_amount(line: OrderLine, base):
    if line.discount_kind == "percentage":
        return percent_of(base, line.discount_value)
    if line.discount_kind == "fixed":
        return min(money(line.discount_value), base)
    return ZERO


def compute_totals(order: Order) -> OrderTotals:
    if not order.lines:
        return OrderTotals()
    default_rate = D(settings_svc.get("tax.rate_pct"))
    lines: list[LinePricing] = []
    for ln in order.lines:
        base = _line_base(ln)
        ld = _line_discount_amount(ln, base)
        lines.append(LinePricing(line=ln, base=base, line_discount=ld,
                                 net_after_line_discount=money(base - ld)))

    pre_order_discount = money(sum((lp.net_after_line_discount for lp in lines), ZERO))
    if order.discount_kind == "percentage":
        order_discount = percent_of(pre_order_discount, order.discount_value)
    elif order.discount_kind == "fixed":
        order_discount = min(money(order.discount_value), pre_order_discount)
    else:
        order_discount = ZERO

    weights = [lp.net_after_line_discount for lp in lines]
    shares = (allocate(order_discount, weights) if order_discount > 0 and pre_order_discount > 0
             else [ZERO] * len(lines))
    for lp, share in zip(lines, shares, strict=True):
        lp.order_discount_share = share
        lp.taxable_base = money(lp.net_after_line_discount - share)
        rate = lp.line.item.tax_rate_pct if lp.line.item.tax_rate_pct is not None else default_rate
        lp.tax = percent_of(lp.taxable_base, rate)

    subtotal = money(sum((lp.base for lp in lines), ZERO))
    line_discounts = money(sum((lp.line_discount for lp in lines), ZERO))
    discount_total = money(line_discounts + order_discount)
    net_of_discount = money(sum((lp.taxable_base for lp in lines), ZERO))
    tax_total = money(sum((lp.tax for lp in lines), ZERO))

    sc_pct = D(settings_svc.get("service_charge.percent"))
    sc_applies = sc_pct > 0 and (order.order_type == "dine_in"
                                 or not settings_svc.get("service_charge.dine_in_only"))
    service_charge = percent_of(net_of_discount, sc_pct) if sc_applies else ZERO
    if service_charge > 0 and settings_svc.get("service_charge.taxable"):
        tax_total = money(tax_total + percent_of(service_charge, default_rate))

    delivery = money(order.delivery_charge) if order.order_type == "delivery" else ZERO
    total = money(net_of_discount + tax_total + service_charge + delivery)

    return OrderTotals(lines=lines, subtotal=subtotal, discount_total=discount_total,
                       tax_total=tax_total, service_charge_total=service_charge,
                       delivery_charge_total=delivery, total=total)


# ============================================================================ completing a sale ==

def _consume_for_line(item: InventoryItem, location, quantity, actor, reference_id):
    recipe = db.session.scalar(db.select(Recipe).where(Recipe.item_id == item.id, Recipe.is_active.is_(True)))
    if recipe:
        return recipe_svc.consume_for_output(recipe, location, quantity, actor, reference_type="sale",
                                             reference_id=reference_id, reason=f"Sale {reference_id}")
    return inv_svc.consume_stock(item=item, location=location, quantity=quantity, actor=actor,
                                movement_type="consumption", reference_type="sale",
                                reference_id=reference_id, reason=f"Sale {reference_id}")


def complete_sale(actor, order: Order, payments_data: list[dict]) -> Invoice:
    """The orchestrator. All-or-nothing within the caller's transaction."""
    _ensure_open(order)
    if not order.lines:
        raise BusinessRuleError("Add at least one item before completing the sale.")

    totals = compute_totals(order)
    errors, clean_payments = {}, []
    cash_rows = 0
    for i, p in enumerate(payments_data):
        method = p.get("method")
        try:
            amount = money(p.get("amount"))
            assert amount > 0
        except Exception:  # noqa: BLE001
            errors[f"payment{i}"] = "Enter a valid payment amount."
            continue
        if method not in ("cash", "card", "bank_transfer", "mobile_wallet", "other"):
            errors[f"payment{i}"] = "Choose a valid payment method."
            continue
        if method == "cash":
            cash_rows += 1
        clean_payments.append({"method": method, "amount": amount,
                               "reference": (p.get("reference") or "").strip()[:120] or None})
    if cash_rows > 1:
        errors["payments"] = "Only one cash payment line is allowed per sale."
    if not clean_payments and not errors:
        errors["payments"] = "Record at least one payment."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    paid_total = money(sum((p["amount"] for p in clean_payments), ZERO))
    change_due = money(max(ZERO, paid_total - totals.total))
    if change_due > 0 and cash_rows == 0:
        raise BusinessRuleError("The amount paid exceeds the total; change can only be given in cash.")
    if paid_total < totals.total:
        raise BusinessRuleError(
            f"Payment of {paid_total} does not cover the total of {totals.total}."
        )

    session = None
    if cash_rows:
        session = cash_svc.open_session_for(order.location_id)
        if session is None and settings_svc.get("pos.cash_requires_open_session"):
            raise BusinessRuleError(
                "No cash register session is open at this location. Open one before taking cash."
            )

    # 1) deduct inventory for every line, and capture the ACTUAL cost incurred (for COGS)
    cogs_total = ZERO
    for lp in totals.lines:
        movements = _consume_for_line(lp.line.item, order.location, lp.line.quantity, actor, order.id)
        cogs_total += sum((D(m.qty) * -1 * D(m.unit_cost) for m in movements), ZERO)
    cogs_total = money(cogs_total)

    # 2) invoice
    invoice = Invoice(
        invoice_number=_next_number("pos.invoice_number_prefix", Invoice), order_id=order.id,
        branch_id=order.branch_id, subtotal=totals.subtotal, discount_total=totals.discount_total,
        tax_total=totals.tax_total, service_charge_total=totals.service_charge_total,
        delivery_charge_total=totals.delivery_charge_total, total=totals.total,
        paid_total=money(min(paid_total, totals.total)), status="paid", cogs_total=cogs_total,
    )
    db.session.add(invoice)
    db.session.flush()

    # 3) payments (+ cash register)
    for p in clean_payments:
        pay_amount = p["amount"] - change_due if p["method"] == "cash" else p["amount"]
        db.session.add(Payment(invoice_id=invoice.id, branch_id=order.branch_id,
                               cash_session_id=session.id if (p["method"] == "cash" and session) else None,
                               method=p["method"], amount=p["amount"], reference=p["reference"],
                               received_by=actor.id if actor else None))
        if p["method"] == "cash" and session:
            cash_svc.record_sale_cash(session, pay_amount, reference_type="invoice",
                                      reference_id=invoice.id, actor=actor)

    # 4) close out the order and free the table
    order.status, order.completed_at = "completed", datetime.now(UTC)
    if order.table_id:
        order.table.status = "available"

    audit.log("order.complete", "pos", record_type="invoice", record_id=invoice.id,
             branch_id=order.branch_id, reference=invoice.invoice_number,
             after={"total": str(totals.total), "cogs": str(cogs_total), "paid": str(paid_total)})
    return invoice


def reprint_invoice(actor, invoice: Invoice) -> None:
    invoice.print_count += 1
    audit.log("invoice.print", "pos", record_type="invoice", record_id=invoice.id,
             branch_id=invoice.branch_id, reference=invoice.invoice_number,
             after={"print_count": invoice.print_count})
