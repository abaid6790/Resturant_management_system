from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
MONEY = Numeric(14, 2)
RATE = Numeric(9, 4)

TABLE_STATUSES = ("available", "occupied", "reserved", "cleaning")
ORDER_TYPES = ("dine_in", "takeaway", "delivery")
ORDER_STATUSES = ("open", "completed", "void")
DISCOUNT_KINDS = ("none", "percentage", "fixed")
INVOICE_STATUSES = ("unpaid", "partial", "paid")
PAYMENT_METHODS = ("cash", "card", "bank_transfer", "mobile_wallet", "other")
CASH_SESSION_STATUSES = ("open", "closed")
CASH_MOVEMENT_TYPES = ("opening", "cash_in", "cash_out", "sale", "closing_count")


class Floor(TimestampMixin, db.Model):
    __tablename__ = "floors"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    branch = relationship("Branch")
    tables: Mapped[list[Table]] = relationship(back_populates="floor", order_by="Table.name")

    __table_args__ = (UniqueConstraint("branch_id", "name", name="uq_floor_branch_name"),)


class Table(TimestampMixin, db.Model):
    __tablename__ = "tables"

    id: Mapped[int] = mapped_column(primary_key=True)
    floor_id: Mapped[int] = mapped_column(ForeignKey("floors.id", ondelete="CASCADE"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(40))
    capacity: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    status: Mapped[str] = mapped_column(String(12), default="available", server_default="available")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    floor: Mapped[Floor] = relationship(back_populates="tables")
    branch = relationship("Branch")

    __table_args__ = (
        UniqueConstraint("floor_id", "name", name="uq_table_floor_name"),
        CheckConstraint("status in " + str(TABLE_STATUSES), name="valid_table_status"),
        CheckConstraint("capacity > 0", name="positive_capacity"),
    )


class Customer(TimestampMixin, db.Model):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class ProductModifier(db.Model):
    """An optional add-on for a specific item, e.g. 'Extra cheese' (+1.00) on a burger."""

    __tablename__ = "product_modifiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60))
    price_delta: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    item = relationship("InventoryItem")

    __table_args__ = (UniqueConstraint("item_id", "name", name="uq_modifier_item_name"),)


class Order(TimestampMixin, db.Model):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_number: Mapped[str] = mapped_column(String(30), unique=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    order_type: Mapped[str] = mapped_column(String(10))
    table_id: Mapped[int | None] = mapped_column(ForeignKey("tables.id", ondelete="SET NULL"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    guest_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    notes: Mapped[str | None] = mapped_column(Text)
    discount_kind: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    discount_value: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    discount_reason: Mapped[str | None] = mapped_column(String(255))
    delivery_charge: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    server_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(255))

    branch = relationship("Branch")
    location = relationship("Location", lazy="joined")
    table: Mapped[Table | None] = relationship(lazy="joined")
    customer: Mapped[Customer | None] = relationship(lazy="joined")
    server = relationship("User", foreign_keys=[server_id])
    lines: Mapped[list[OrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.id"
    )
    invoice: Mapped[Invoice | None] = relationship(back_populates="order", uselist=False)

    __table_args__ = (
        CheckConstraint("order_type in " + str(ORDER_TYPES), name="valid_order_type"),
        CheckConstraint("status in " + str(ORDER_STATUSES), name="valid_order_status"),
        CheckConstraint("discount_kind in " + str(DISCOUNT_KINDS), name="valid_discount_kind"),
        CheckConstraint("discount_value >= 0", name="discount_value_nonneg"),
        CheckConstraint("delivery_charge >= 0", name="delivery_charge_nonneg"),
        Index("ix_orders_branch_status", "branch_id", "status"),
        # Only one OPEN order may reference a given table at a time. Created via raw SQL in
        # migration 0006 (Alembic autogenerate does not emit partial indexes on its own); declared
        # here too so the model metadata matches the database exactly (see test_models_and_
        # migrations_agree) rather than drifting out of sync with what the migration actually did.
        Index("uq_orders_one_open_per_table", "table_id", unique=True,
             postgresql_where=db.text("status = 'open' AND table_id IS NOT NULL")),
    )


class OrderLine(db.Model):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"))
    quantity: Mapped[object] = mapped_column(QTY)
    unit_price: Mapped[object] = mapped_column(MONEY)  # snapshot at time of adding
    notes: Mapped[str | None] = mapped_column(String(255))
    discount_kind: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    discount_value: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")

    order: Mapped[Order] = relationship(back_populates="lines")
    item = relationship("InventoryItem", lazy="joined")
    modifiers: Mapped[list[OrderLineModifier]] = relationship(
        back_populates="line", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_line_qty"),
        CheckConstraint("discount_kind in " + str(DISCOUNT_KINDS), name="valid_line_discount_kind"),
        CheckConstraint("discount_value >= 0", name="line_discount_value_nonneg"),
    )


class OrderLineModifier(db.Model):
    """Snapshot of a chosen modifier: name/price at the time it was added, like an order line."""

    __tablename__ = "order_line_modifiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60))
    price_delta: Mapped[object] = mapped_column(MONEY)

    line: Mapped[OrderLine] = relationship(back_populates="modifiers")


class Invoice(TimestampMixin, db.Model):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(30), unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), unique=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    subtotal: Mapped[object] = mapped_column(MONEY)
    discount_total: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    tax_total: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    service_charge_total: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    delivery_charge_total: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    total: Mapped[object] = mapped_column(MONEY)
    paid_total: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(10), default="unpaid", server_default="unpaid")
    cogs_total: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    print_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    order: Mapped[Order] = relationship(back_populates="invoice")
    branch = relationship("Branch")
    payments: Mapped[list[Payment]] = relationship(back_populates="invoice", order_by="Payment.id")

    __table_args__ = (
        CheckConstraint("status in " + str(INVOICE_STATUSES), name="valid_sale_invoice_status"),
        CheckConstraint("total >= 0", name="sale_invoice_total_nonneg"),
    )


class Payment(TimestampMixin, db.Model):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    cash_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("cash_register_sessions.id", ondelete="SET NULL")
    )
    method: Mapped[str] = mapped_column(String(20))
    amount: Mapped[object] = mapped_column(MONEY)
    reference: Mapped[str | None] = mapped_column(String(120))
    received_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    invoice: Mapped[Invoice] = relationship(back_populates="payments")

    __table_args__ = (
        CheckConstraint("method in " + str(PAYMENT_METHODS), name="valid_payment_method"),
        CheckConstraint("amount > 0", name="positive_payment"),
    )


class CashRegisterSession(TimestampMixin, db.Model):
    __tablename__ = "cash_register_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    opened_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opening_float: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counted_cash: Mapped[object | None] = mapped_column(MONEY)
    expected_cash: Mapped[object | None] = mapped_column(MONEY)
    variance: Mapped[object | None] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    notes: Mapped[str | None] = mapped_column(Text)

    location = relationship("Location", lazy="joined")
    branch = relationship("Branch")

    __table_args__ = (
        CheckConstraint("status in " + str(CASH_SESSION_STATUSES), name="valid_cash_session_status"),
    )


class CashMovement(db.Model):
    """Append-only, like the other ledgers (DB trigger blocks UPDATE/DELETE)."""

    __tablename__ = "cash_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    session_id: Mapped[int] = mapped_column(ForeignKey("cash_register_sessions.id", ondelete="RESTRICT"),
                                            index=True)
    movement_type: Mapped[str] = mapped_column(String(15))
    amount: Mapped[object] = mapped_column(MONEY)  # signed: +in, -out
    reason: Mapped[str | None] = mapped_column(String(255))
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[str | None] = mapped_column(String(40))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    __table_args__ = (
        CheckConstraint("movement_type in " + str(CASH_MOVEMENT_TYPES), name="valid_cash_movement_type"),
    )
