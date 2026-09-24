from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from .mixins import TimestampMixin

QTY = Numeric(16, 4)
COST = Numeric(18, 6)
MONEY = Numeric(14, 2)

PO_STATUSES = ("draft", "submitted", "approved", "rejected", "partially_received", "received",
              "cancelled")
INVOICE_STATUSES = ("unpaid", "partial", "paid")
LEDGER_ENTRY_TYPES = ("invoice", "payment", "return", "adjustment", "opening")


class Supplier(TimestampMixin, db.Model):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (CheckConstraint("payment_terms_days >= 0", name="terms_nonneg"),)


class SupplierItem(db.Model):
    """Optional: this supplier's own SKU/price for an item, for quick reordering."""

    __tablename__ = "supplier_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"))
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"))
    supplier_sku: Mapped[str | None] = mapped_column(String(60))
    last_cost: Mapped[object | None] = mapped_column(COST)
    is_preferred: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    supplier: Mapped[Supplier] = relationship()
    item = relationship("InventoryItem")

    __table_args__ = (UniqueConstraint("supplier_id", "item_id", name="uq_supplier_item"),)


class PurchaseOrder(TimestampMixin, db.Model):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(30), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    order_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    expected_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(String(255))
    cancelled_reason: Mapped[str | None] = mapped_column(String(255))

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    branch = relationship("Branch")
    location = relationship("Location", lazy="joined")
    lines: Mapped[list[PurchaseOrderLine]] = relationship(
        back_populates="po", cascade="all, delete-orphan", order_by="PurchaseOrderLine.id"
    )

    __table_args__ = (
        CheckConstraint("status in " + str(PO_STATUSES), name="valid_po_status"),
        Index("ix_purchase_orders_branch_id", "branch_id"),
        Index("ix_purchase_orders_supplier_id", "supplier_id"),
    )


class PurchaseOrderLine(db.Model):
    __tablename__ = "purchase_order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"))
    packaging_unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("item_packaging_units.id", ondelete="SET NULL")
    )
    quantity_ordered: Mapped[object] = mapped_column(QTY)  # always in the item's stock unit
    quantity_received: Mapped[object] = mapped_column(QTY, default=0, server_default="0")
    unit_cost: Mapped[object] = mapped_column(COST)  # per stock unit

    po: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    item = relationship("InventoryItem", lazy="joined")
    packaging_unit = relationship("ItemPackagingUnit")

    __table_args__ = (
        CheckConstraint("quantity_ordered > 0", name="positive_qty_ordered"),
        CheckConstraint("quantity_received >= 0", name="qty_received_nonneg"),
        CheckConstraint("quantity_received <= quantity_ordered", name="qty_received_le_ordered"),
    )


class PurchaseReceipt(TimestampMixin, db.Model):
    """One goods-received event against a PO. A PO can be received in several deliveries."""

    __tablename__ = "purchase_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    received_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reference: Mapped[str | None] = mapped_column(String(120))

    po: Mapped[PurchaseOrder] = relationship()
    lines: Mapped[list[PurchaseReceiptLine]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class PurchaseReceiptLine(db.Model):
    __tablename__ = "purchase_receipt_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("purchase_receipts.id", ondelete="CASCADE"))
    po_line_id: Mapped[int] = mapped_column(ForeignKey("purchase_order_lines.id", ondelete="RESTRICT"))
    quantity: Mapped[object] = mapped_column(QTY)
    unit_cost: Mapped[object] = mapped_column(COST)
    batch_no: Mapped[str | None] = mapped_column(String(60))
    expiry_date: Mapped[date | None] = mapped_column(Date)

    receipt: Mapped[PurchaseReceipt] = relationship(back_populates="lines")
    po_line: Mapped[PurchaseOrderLine] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("quantity > 0", name="positive_receipt_qty"),)


class PurchaseInvoice(TimestampMixin, db.Model):
    __tablename__ = "purchase_invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(60))
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"))
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id", ondelete="SET NULL"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    invoice_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    due_date: Mapped[date | None] = mapped_column(Date)
    subtotal: Mapped[object] = mapped_column(MONEY)
    discount_total: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    tax_total: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    total: Mapped[object] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(10), default="unpaid", server_default="unpaid")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    po: Mapped[PurchaseOrder | None] = relationship()

    __table_args__ = (
        CheckConstraint("status in " + str(INVOICE_STATUSES), name="valid_invoice_status"),
        CheckConstraint("total >= 0", name="invoice_total_nonneg"),
        UniqueConstraint("supplier_id", "invoice_number", name="uq_supplier_invoice_number"),
        Index("ix_purchase_invoices_branch_id", "branch_id"),
    )


class SupplierPayment(TimestampMixin, db.Model):
    __tablename__ = "supplier_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"))
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_invoices.id", ondelete="SET NULL"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    amount: Mapped[object] = mapped_column(MONEY)
    method: Mapped[str] = mapped_column(String(20), default="cash", server_default="cash")
    paid_at: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    reference: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(String(255))
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    invoice: Mapped[PurchaseInvoice | None] = relationship()

    __table_args__ = (CheckConstraint("amount > 0", name="positive_payment_amount"),)


class SupplierBalance(db.Model):
    """Cached current balance owed to a supplier. Always re-derivable from supplier_ledger_entries."""

    __tablename__ = "supplier_balances"

    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"),
                                              primary_key=True)
    balance: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class SupplierLedgerEntry(db.Model):
    """Append-only, like stock_movements and audit_logs (DB trigger blocks UPDATE/DELETE)."""

    __tablename__ = "supplier_ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"), index=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    entry_type: Mapped[str] = mapped_column(String(12))
    amount: Mapped[object] = mapped_column(MONEY)  # signed: +invoice increases owed, -payment/-return
    previous_balance: Mapped[object] = mapped_column(MONEY)
    new_balance: Mapped[object] = mapped_column(MONEY)
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    supplier: Mapped[Supplier] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("entry_type in " + str(LEDGER_ENTRY_TYPES), name="valid_ledger_entry_type"),
    )


class PurchaseReturn(TimestampMixin, db.Model):
    __tablename__ = "purchase_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(String(255))
    total: Mapped[object] = mapped_column(MONEY)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    location = relationship("Location")
    lines: Mapped[list[PurchaseReturnLine]] = relationship(
        back_populates="ret", cascade="all, delete-orphan"
    )


class PurchaseReturnLine(db.Model):
    __tablename__ = "purchase_return_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    return_id: Mapped[int] = mapped_column(ForeignKey("purchase_returns.id", ondelete="CASCADE"))
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"))
    quantity: Mapped[object] = mapped_column(QTY)
    unit_cost: Mapped[object] = mapped_column(COST)

    ret: Mapped[PurchaseReturn] = relationship(back_populates="lines")
    item = relationship("InventoryItem", lazy="joined")

    __table_args__ = (CheckConstraint("quantity > 0", name="positive_return_qty"),)
