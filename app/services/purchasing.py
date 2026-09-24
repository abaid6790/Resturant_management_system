"""
Suppliers and purchasing. Mirrors the pattern used by the stock ledger: the supplier ledger is
append-only (DB trigger blocks UPDATE/DELETE), a cached SupplierBalance is kept in step with it,
and every function here posts within the CALLER's transaction (no commits in this module), so a
purchase event that touches stock, the PO, and the supplier ledger commits or rolls back as one.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.money import D, money
from app.core.money import qty as qround
from app.core.money import unit_cost as cround
from app.extensions import db
from app.models.inventory import InventoryItem, ItemPackagingUnit, Location
from app.models.purchasing import (
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
    PurchaseReturn,
    PurchaseReturnLine,
    Supplier,
    SupplierBalance,
    SupplierLedgerEntry,
    SupplierPayment,
)
from app.services import audit
from app.services import inventory as inv_svc
from app.services import settings as settings_svc

CODE_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{1,19}")


# ============================================================================ suppliers =========

def search_suppliers(q="", active_only=True):
    stmt = db.select(Supplier).order_by(Supplier.name)
    if active_only:
        stmt = stmt.where(Supplier.is_active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(db.or_(Supplier.name.ilike(like), Supplier.code.ilike(like)))
    return stmt


def get_supplier_or_404(supplier_id: int) -> Supplier:
    s = db.session.get(Supplier, supplier_id)
    if s is None:
        raise NotFoundError("Supplier not found.")
    return s


def _clean_supplier(data: dict, existing: Supplier | None) -> dict:
    errors, out = {}, {}
    code = (data.get("code") or "").strip().upper()
    if not CODE_RE.fullmatch(code):
        errors["code"] = "Use 2–20 letters, numbers, dots, dashes or underscores."
    else:
        clash = db.session.scalar(db.select(Supplier).where(Supplier.code == code))
        if clash and (existing is None or clash.id != existing.id):
            errors["code"] = "Another supplier already uses this code."
    out["code"] = code
    out["name"] = (data.get("name") or "").strip()
    if not out["name"] or len(out["name"]) > 150:
        errors["name"] = "Enter a supplier name."
    out["contact_name"] = (data.get("contact_name") or "").strip()[:120] or None
    out["phone"] = (data.get("phone") or "").strip()[:40] or None
    email = (data.get("email") or "").strip()
    if email and ("@" not in email or len(email) > 255):
        errors["email"] = "Enter a valid email address."
    out["email"] = email or None
    out["address"] = (data.get("address") or "").strip() or None
    out["notes"] = (data.get("notes") or "").strip() or None
    terms = (data.get("payment_terms_days") or "0").strip() or "0"
    if not terms.isdigit():
        errors["payment_terms_days"] = "Enter a whole number of days."
    else:
        out["payment_terms_days"] = int(terms)
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    return out


def _snap_supplier(s: Supplier) -> dict:
    return {"code": s.code, "name": s.name, "phone": s.phone, "email": s.email,
            "payment_terms_days": s.payment_terms_days}


def create_supplier(actor, data: dict) -> Supplier:
    clean = _clean_supplier(data, None)
    s = Supplier(**clean, is_active=True)
    db.session.add(s)
    db.session.flush()
    audit.log("supplier.create", "purchasing", record_type="supplier", record_id=s.id,
             after=_snap_supplier(s))
    return s


def update_supplier(actor, s: Supplier, data: dict) -> Supplier:
    before = _snap_supplier(s)
    for k, v in _clean_supplier(data, s).items():
        setattr(s, k, v)
    audit.log_change("supplier.update", "purchasing", before, _snap_supplier(s),
                     record_type="supplier", record_id=s.id)
    return s


def set_supplier_active(actor, s: Supplier, active: bool) -> None:
    if s.is_active != active:
        s.is_active = active
        audit.log("supplier.activate" if active else "supplier.deactivate", "purchasing",
                  record_type="supplier", record_id=s.id)


def supplier_balance(supplier_id: int):
    row = db.session.get(SupplierBalance, supplier_id)
    return row.balance if row else D("0.00")


def _balance_row(supplier_id: int) -> SupplierBalance:
    row = db.session.get(SupplierBalance, supplier_id)
    if row is None:
        row = SupplierBalance(supplier_id=supplier_id, balance=D("0.00"))
        db.session.add(row)
        db.session.flush()
    return row


def _post_ledger(*, supplier_id, branch_id, entry_type, amount, reference_type, reference_id,
                 notes, actor) -> SupplierLedgerEntry:
    """`amount` is signed: positive increases what's owed, negative decreases it."""
    bal = _balance_row(supplier_id)
    previous = bal.balance
    new_balance = money(D(previous) + D(amount))
    bal.balance = new_balance
    entry = SupplierLedgerEntry(
        supplier_id=supplier_id, branch_id=branch_id, entry_type=entry_type, amount=money(amount),
        previous_balance=previous, new_balance=new_balance, reference_type=reference_type,
        reference_id=None if reference_id is None else str(reference_id), notes=notes,
        user_id=actor.id if actor else None,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def supplier_ledger_stmt(supplier_id: int):
    return (db.select(SupplierLedgerEntry).where(SupplierLedgerEntry.supplier_id == supplier_id)
           .order_by(SupplierLedgerEntry.id.desc()))


# ============================================================================ purchase orders ====

def _next_po_number() -> str:
    prefix = settings_svc.get("purchasing.po_number_prefix") or "PO"
    n = (db.session.scalar(db.select(db.func.count(PurchaseOrder.id))) or 0) + 1
    while db.session.scalar(db.select(PurchaseOrder.id)
                            .where(PurchaseOrder.po_number == f"{prefix}-{n:06d}")):
        n += 1
    return f"{prefix}-{n:06d}"


def visible_pos(actor, status: str | None = None, supplier_id: int | None = None):
    from app.core.authz import allowed_branch_ids
    stmt = db.select(PurchaseOrder).order_by(PurchaseOrder.id.desc())
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(PurchaseOrder.branch_id.in_(ids))
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    return stmt


def get_po_or_404(actor, po_id: int) -> PurchaseOrder:
    from app.core.authz import allowed_branch_ids
    po = db.session.get(PurchaseOrder, po_id)
    ids = allowed_branch_ids(actor)
    if po is None or (ids is not None and po.branch_id not in ids):
        raise NotFoundError("Purchase order not found.")
    return po


def _line_conversion(item: InventoryItem, packaging_unit_id) -> tuple:
    """Return (factor, packaging_unit_or_None). Quantities/costs entered in the order unit are
    divided/multiplied by `factor` to get the item's own stock unit."""
    if not packaging_unit_id:
        return D(1), None
    pkg = db.session.get(ItemPackagingUnit, int(packaging_unit_id))
    if pkg is None or pkg.item_id != item.id:
        raise ValidationError("Please fix the highlighted fields.",
                              details={"packaging_unit_id": "Choose a valid packaging unit for this item."})
    return D(pkg.factor_to_stock_unit), pkg


def _clean_po_lines(raw_lines: list[dict]) -> list[dict]:
    errors, clean, seen = {}, [], set()
    for i, r in enumerate(raw_lines):
        if not r.get("item_id"):
            continue
        item = db.session.get(InventoryItem, int(r["item_id"]))
        if item is None:
            errors[f"line{i}"] = "Unknown item."
            continue
        if item.id in seen:
            errors[f"line{i}"] = f"{item.name} is listed twice."
            continue
        seen.add(item.id)
        try:
            qty_entered = qround(r.get("quantity"))
            assert qty_entered > 0
        except Exception:  # noqa: BLE001
            errors[f"line{i}"] = f"Enter a quantity greater than zero for {item.name}."
            continue
        try:
            cost_entered = D(r.get("unit_cost"))
            assert cost_entered >= 0
        except Exception:  # noqa: BLE001
            errors[f"line{i}"] = f"Enter a valid cost for {item.name}."
            continue
        try:
            factor, pkg = _line_conversion(item, r.get("packaging_unit_id"))
        except ValidationError:
            errors[f"line{i}"] = f"Invalid packaging unit for {item.name}."
            continue
        clean.append({"item": item, "quantity_ordered": qround(qty_entered * factor),
                      "unit_cost": cround(cost_entered / factor),
                      "packaging_unit_id": pkg.id if pkg else None})
    if not clean and not errors:
        errors["lines"] = "Add at least one item."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    return clean


def _snap_po(po: PurchaseOrder) -> dict:
    return {"status": po.status, "supplier_id": po.supplier_id, "location_id": po.location_id,
            "lines": sorted((line.item.sku, str(line.quantity_ordered), str(line.unit_cost))
                            for line in po.lines)}


def create_po(actor, data: dict) -> PurchaseOrder:
    from app.core.authz import allowed_branch_ids
    errors = {}
    supplier = db.session.get(Supplier, int(data["supplier_id"])) \
        if str(data.get("supplier_id") or "").isdigit() else None
    if supplier is None or not supplier.is_active:
        errors["supplier_id"] = "Choose an active supplier."
    location = db.session.get(Location, int(data["location_id"])) \
        if str(data.get("location_id") or "").isdigit() else None
    ids = allowed_branch_ids(actor)
    # Same rule as everywhere else (ADR-010): a location outside the actor's branches is treated
    # as if it does not exist, not surfaced as "yours but forbidden" (404, not 403).
    if location is None or (ids is not None and location.branch_id not in ids):
        errors["location_id"] = "Choose a delivery location."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    lines = _clean_po_lines(data.get("lines") or [])

    po = PurchaseOrder(po_number=_next_po_number(), supplier_id=supplier.id,
                       branch_id=location.branch_id, location_id=location.id, status="draft",
                       expected_date=data.get("expected_date") or None,
                       notes=(data.get("notes") or "").strip() or None, created_by=actor.id)
    db.session.add(po)
    db.session.flush()
    po.lines = [PurchaseOrderLine(**line) for line in lines]
    db.session.flush()
    audit.log("purchase_order.create", "purchasing", record_type="purchase_order", record_id=po.id,
             branch_id=po.branch_id, reference=po.po_number, after=_snap_po(po))
    return po


def update_po(actor, po: PurchaseOrder, data: dict) -> PurchaseOrder:
    if po.status != "draft":
        raise BusinessRuleError("Only a draft purchase order can be edited.")
    before = _snap_po(po)
    lines = _clean_po_lines(data.get("lines") or [])
    po.expected_date = data.get("expected_date") or None
    po.notes = (data.get("notes") or "").strip() or None
    po.lines.clear()
    db.session.flush()
    po.lines = [PurchaseOrderLine(**line) for line in lines]
    db.session.flush()
    audit.log_change("purchase_order.update", "purchasing", before, _snap_po(po),
                     record_type="purchase_order", record_id=po.id, branch_id=po.branch_id,
                     reference=po.po_number)
    return po


def submit_po(actor, po: PurchaseOrder) -> PurchaseOrder:
    if po.status != "draft":
        raise BusinessRuleError("Only a draft purchase order can be submitted.")
    if not po.lines:
        raise BusinessRuleError("Add at least one item before submitting.")
    requires_approval = bool(settings_svc.get("purchasing.require_approval"))
    po.status = "submitted" if requires_approval else "approved"
    if not requires_approval:
        po.approved_by, po.approved_at = actor.id, db.func.now()
    audit.log("purchase_order.submit", "purchasing", record_type="purchase_order", record_id=po.id,
             branch_id=po.branch_id, reference=po.po_number, after={"status": po.status})
    return po


def approve_po(actor, po: PurchaseOrder) -> PurchaseOrder:
    if po.status != "submitted":
        raise BusinessRuleError("Only a submitted purchase order can be approved.")
    po.status, po.approved_by, po.approved_at = "approved", actor.id, db.func.now()
    audit.log("purchase_order.approve", "purchasing", record_type="purchase_order", record_id=po.id,
             branch_id=po.branch_id, reference=po.po_number)
    return po


def reject_po(actor, po: PurchaseOrder, reason: str) -> PurchaseOrder:
    if po.status != "submitted":
        raise BusinessRuleError("Only a submitted purchase order can be rejected.")
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.", details={"reason": "Required."})
    po.status, po.rejected_reason = "rejected", reason.strip()[:255]
    audit.log("purchase_order.reject", "purchasing", record_type="purchase_order", record_id=po.id,
             branch_id=po.branch_id, reference=po.po_number, after={"reason": po.rejected_reason})
    return po


def cancel_po(actor, po: PurchaseOrder, reason: str) -> PurchaseOrder:
    if po.status in ("received", "cancelled"):
        raise BusinessRuleError("This purchase order can no longer be cancelled.")
    if any(D(line.quantity_received) > 0 for line in po.lines):
        raise BusinessRuleError("A purchase order with received items cannot be cancelled.")
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.", details={"reason": "Required."})
    po.status, po.cancelled_reason = "cancelled", reason.strip()[:255]
    audit.log("purchase_order.cancel", "purchasing", record_type="purchase_order", record_id=po.id,
             branch_id=po.branch_id, reference=po.po_number, after={"reason": po.cancelled_reason})
    return po


# ============================================================================ receiving =========

def receive_po(actor, po: PurchaseOrder, lines_data: list[dict], *, reference=None) -> PurchaseReceipt:
    """
    Receive some or all of a PO's outstanding lines. `lines_data` = [{po_line_id, quantity,
    unit_cost?, batch_no?, expiry_date?}, ...]. Posts one stock movement (and batch, if the item
    tracks batches) PER LINE via the same ledger used everywhere else, so a receipt is exactly as
    traceable as any other stock change. All-or-nothing within this call.
    """
    if po.status not in ("approved", "partially_received"):
        raise BusinessRuleError(
            "This purchase order is not ready to receive." if po.status != "submitted"
            else "This purchase order needs approval before it can be received."
        )
    by_id = {line.id: line for line in po.lines}
    errors, clean = {}, []
    for i, r in enumerate(lines_data):
        line = by_id.get(int(r.get("po_line_id", 0)))
        if line is None:
            continue
        outstanding = D(line.quantity_ordered) - D(line.quantity_received)
        try:
            qty = qround(r.get("quantity"))
        except Exception:  # noqa: BLE001
            continue
        if qty <= 0:
            continue
        if qty > outstanding:
            errors[f"line{i}"] = (f"Only {outstanding} {line.item.stock_unit.code} of "
                                  f"{line.item.name} is still outstanding.")
            continue
        cost = D(r["unit_cost"]) if r.get("unit_cost") else line.unit_cost
        clean.append({"line": line, "quantity": qty, "unit_cost": cround(cost),
                      "batch_no": (r.get("batch_no") or "").strip() or None,
                      "expiry_date": r.get("expiry_date") or None})
    if not clean and not errors:
        raise ValidationError("Enter a quantity for at least one item.")
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    receipt = PurchaseReceipt(po_id=po.id, received_by=actor.id if actor else None,
                              reference=reference)
    db.session.add(receipt)
    db.session.flush()
    location = po.location
    for c in clean:
        line = c["line"]
        inv_svc.receive_stock(item=line.item, location=location, quantity=c["quantity"],
                              unit_cost=c["unit_cost"], actor=actor, batch_no=c["batch_no"],
                              expiry_date=c["expiry_date"], source_type="purchase",
                              source_id=receipt.id, movement_type="purchase_in",
                              reference_type="purchase_receipt", reference_id=receipt.id,
                              reason=f"Received against {po.po_number}")
        line.quantity_received = qround(D(line.quantity_received) + c["quantity"])
        db.session.add(PurchaseReceiptLine(receipt_id=receipt.id, po_line_id=line.id,
                                           quantity=c["quantity"], unit_cost=c["unit_cost"],
                                           batch_no=c["batch_no"], expiry_date=c["expiry_date"]))
    db.session.flush()
    po.status = ("received" if all(D(pl.quantity_received) >= D(pl.quantity_ordered) for pl in po.lines)
                else "partially_received")
    audit.log("purchase_order.receive", "purchasing", record_type="purchase_receipt",
             record_id=receipt.id, branch_id=po.branch_id, reference=po.po_number,
             after={"status": po.status, "lines": len(clean)})
    return receipt


# ============================================================================ invoices ===========

def create_invoice(actor, po: PurchaseOrder, data: dict) -> PurchaseInvoice:
    errors = {}
    number = (data.get("invoice_number") or "").strip()
    if not number:
        errors["invoice_number"] = "Enter the supplier's invoice number."
    elif db.session.scalar(db.select(PurchaseInvoice).where(
            PurchaseInvoice.supplier_id == po.supplier_id, PurchaseInvoice.invoice_number == number)):
        errors["invoice_number"] = "An invoice with this number already exists for this supplier."
    try:
        subtotal = money(data.get("subtotal"))
        assert subtotal >= 0
    except Exception:  # noqa: BLE001
        errors["subtotal"] = "Enter a valid amount."
        subtotal = D("0.00")
    tax = money(data.get("tax_total") or "0")
    discount = money(data.get("discount_total") or "0")
    total = money(subtotal + tax - discount)
    if total < 0:
        errors["discount_total"] = "Discount cannot exceed the subtotal plus tax."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    due = data.get("due_date") or None
    if not due:
        terms = po.supplier.payment_terms_days or settings_svc.get("purchasing.default_payment_terms_days")
        due = date.today() + timedelta(days=terms)
    invoice = PurchaseInvoice(invoice_number=number, supplier_id=po.supplier_id, po_id=po.id,
                              branch_id=po.branch_id, due_date=due, subtotal=subtotal,
                              tax_total=tax, discount_total=discount, total=total,
                              created_by=actor.id if actor else None)
    db.session.add(invoice)
    db.session.flush()
    _post_ledger(supplier_id=po.supplier_id, branch_id=po.branch_id, entry_type="invoice",
                amount=total, reference_type="purchase_invoice", reference_id=invoice.id,
                notes=f"Invoice {number} ({po.po_number})", actor=actor)
    audit.log("purchase_invoice.create", "purchasing", record_type="purchase_invoice",
             record_id=invoice.id, branch_id=po.branch_id, reference=number,
             after={"total": str(total)})
    return invoice


def _update_invoice_status(invoice: PurchaseInvoice) -> None:
    paid = db.session.scalar(
        db.select(db.func.coalesce(db.func.sum(SupplierPayment.amount), 0))
        .where(SupplierPayment.invoice_id == invoice.id)
    )
    if paid <= 0:
        invoice.status = "unpaid"
    elif D(paid) >= D(invoice.total):
        invoice.status = "paid"
    else:
        invoice.status = "partial"


# ============================================================================ payments ===========

def record_payment(actor, supplier: Supplier, data: dict) -> SupplierPayment:
    errors = {}
    try:
        amount = money(data.get("amount"))
        assert amount > 0
    except Exception:  # noqa: BLE001
        errors["amount"] = "Enter an amount greater than zero."
        amount = D("0.00")
    invoice = None
    if data.get("invoice_id"):
        invoice = db.session.get(PurchaseInvoice, int(data["invoice_id"]))
        if invoice is None or invoice.supplier_id != supplier.id:
            errors["invoice_id"] = "Choose a valid invoice for this supplier."
    method = data.get("method") or "cash"
    if method not in ("cash", "card", "bank_transfer", "mobile_wallet", "cheque"):
        errors["method"] = "Choose a valid payment method."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    from app.core.authz import allowed_branch_ids
    branch_id = data.get("branch_id")
    ids = allowed_branch_ids(actor)
    branch_id = int(branch_id) if branch_id else (invoice.branch_id if invoice else
                                                   (next(iter(ids)) if ids else None))
    payment = SupplierPayment(supplier_id=supplier.id, invoice_id=invoice.id if invoice else None,
                              branch_id=branch_id, amount=amount, method=method,
                              reference=(data.get("reference") or "").strip() or None,
                              notes=(data.get("notes") or "").strip() or None,
                              recorded_by=actor.id if actor else None)
    db.session.add(payment)
    db.session.flush()
    _post_ledger(supplier_id=supplier.id, branch_id=branch_id, entry_type="payment", amount=-amount,
                reference_type="supplier_payment", reference_id=payment.id,
                notes=f"Payment{' for invoice ' + invoice.invoice_number if invoice else ''}",
                actor=actor)
    if invoice:
        _update_invoice_status(invoice)
    audit.log("supplier_payment.create", "purchasing", record_type="supplier_payment",
             record_id=payment.id, branch_id=branch_id, after={"amount": str(amount)})
    return payment


# ============================================================================ returns ============

def create_return(actor, data: dict) -> PurchaseReturn:
    from app.core.authz import allowed_branch_ids
    errors = {}
    supplier = db.session.get(Supplier, int(data["supplier_id"])) \
        if str(data.get("supplier_id") or "").isdigit() else None
    if supplier is None:
        errors["supplier_id"] = "Choose a supplier."
    location = db.session.get(Location, int(data["location_id"])) \
        if str(data.get("location_id") or "").isdigit() else None
    ids = allowed_branch_ids(actor)
    if location is None or (ids is not None and location.branch_id not in ids):
        errors["location_id"] = "Choose a location."
    reason = (data.get("reason") or "").strip()
    if not reason:
        errors["reason"] = "A reason is required."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    raw_lines, clean, seen, line_errors = data.get("lines") or [], [], set(), {}
    for i, r in enumerate(raw_lines):
        if not r.get("item_id"):
            continue
        item = db.session.get(InventoryItem, int(r["item_id"]))
        if item is None or item.id in seen:
            line_errors[f"line{i}"] = "Invalid or duplicate item."
            continue
        seen.add(item.id)
        try:
            qty = qround(r.get("quantity"))
            cost = D(r.get("unit_cost"))
            assert qty > 0 and cost >= 0
        except Exception:  # noqa: BLE001
            line_errors[f"line{i}"] = f"Enter a valid quantity and cost for {item.name}."
            continue
        clean.append({"item": item, "quantity": qty, "unit_cost": cround(cost)})
    if not clean and not line_errors:
        line_errors["lines"] = "Add at least one item to return."
    if line_errors:
        raise ValidationError("Please fix the highlighted fields.", details=line_errors)

    total = money(sum((c["quantity"] * c["unit_cost"] for c in clean), D(0)))
    ret = PurchaseReturn(supplier_id=supplier.id, branch_id=location.branch_id,
                         location_id=location.id, po_id=data.get("po_id") or None, reason=reason,
                         total=total, created_by=actor.id if actor else None)
    db.session.add(ret)
    db.session.flush()
    for c in clean:
        inv_svc.consume_stock(item=c["item"], location=location, quantity=c["quantity"], actor=actor,
                             movement_type="purchase_return", reference_type="purchase_return",
                             reference_id=ret.id, reason=f"Return to {supplier.name}: {reason}")
        db.session.add(PurchaseReturnLine(return_id=ret.id, item_id=c["item"].id,
                                          quantity=c["quantity"], unit_cost=c["unit_cost"]))
    db.session.flush()
    _post_ledger(supplier_id=supplier.id, branch_id=location.branch_id, entry_type="return",
                amount=-total, reference_type="purchase_return", reference_id=ret.id,
                notes=reason, actor=actor)
    audit.log("purchase_return.create", "purchasing", record_type="purchase_return",
             record_id=ret.id, branch_id=location.branch_id, reference=str(ret.id),
             after={"total": str(total)})
    return ret
