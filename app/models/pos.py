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
REFUND_PAYMENT_METHODS = ("cash", "card", "bank_transfer", "mobile_wallet", "other")
PAYMENT_METHODS = (*REFUND_PAYMENT_METHODS, "loyalty_points")
CASH_SESSION_STATUSES = ("open", "closed")
CASH_MOVEMENT_TYPES = ("opening", "cash_in", "cash_out", "sale", "refund", "closing_count")


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
    loyalty_points: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (CheckConstraint("loyalty_points >= 0", name="loyalty_points_nonneg"),)


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
    is_held: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    split_from_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL")
    )

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
    is_fired: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Snapshotted at complete_sale() time, so refunds always use the ORIGINAL transaction's
    # numbers, never a live recalculation that could differ if settings changed since the sale.
    final_net_amount: Mapped[object | None] = mapped_column(MONEY)  # after all discounts, before tax
    final_tax_amount: Mapped[object | None] = mapped_column(MONEY)
    cogs_amount: Mapped[object | None] = mapped_column(MONEY)  # actual cost incurred for this line
    refunded_quantity: Mapped[object] = mapped_column(QTY, default=0, server_default="0")

    order: Mapped[Order] = relationship(back_populates="lines")
    item = relationship("InventoryItem", lazy="joined")
    modifiers: Mapped[list[OrderLineModifier]] = relationship(
        back_populates="line", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_line_qty"),
        CheckConstraint("discount_kind in " + str(DISCOUNT_KINDS), name="valid_line_discount_kind"),
        CheckConstraint("discount_value >= 0", name="line_discount_value_nonneg"),
        CheckConstraint("refunded_quantity >= 0", name="refunded_qty_nonneg"),
        CheckConstraint("refunded_quantity <= quantity", name="refunded_qty_le_quantity"),
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
    refunded_total: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    print_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    refunds: Mapped[list[Refund]] = relationship(back_populates="invoice", order_by="Refund.id")

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
    amount: Mapped[object] = mapped_column(MONEY)  # applied to the bill (cash: net of change given)
    tendered: Mapped[object | None] = mapped_column(MONEY)  # cash actually handed over, if cash
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


class Refund(TimestampMixin, db.Model):
    """A partial or full refund against one invoice. One invoice may have several refunds
    (e.g. a customer returns one item today, another next week)."""

    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    refund_number: Mapped[str] = mapped_column(String(30), unique=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(255))
    net_amount: Mapped[object] = mapped_column(MONEY)
    tax_amount: Mapped[object] = mapped_column(MONEY)
    extras_amount: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    total_amount: Mapped[object] = mapped_column(MONEY)  # net + tax + extras
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    invoice: Mapped[Invoice] = relationship(back_populates="refunds")
    lines: Mapped[list[RefundLine]] = relationship(back_populates="refund",
                                                    cascade="all, delete-orphan")
    payments: Mapped[list[RefundPayment]] = relationship(back_populates="refund",
                                                          cascade="all, delete-orphan")

    __table_args__ = (CheckConstraint("total_amount >= 0", name="refund_total_nonneg"),)


class RefundLine(db.Model):
    __tablename__ = "refund_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    refund_id: Mapped[int] = mapped_column(ForeignKey("refunds.id", ondelete="CASCADE"))
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id", ondelete="RESTRICT"))
    quantity: Mapped[object] = mapped_column(QTY)
    net_amount: Mapped[object] = mapped_column(MONEY)
    tax_amount: Mapped[object] = mapped_column(MONEY)
    restocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    restocked_cost: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")

    refund: Mapped[Refund] = relationship(back_populates="lines")
    order_line: Mapped[OrderLine] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("quantity > 0", name="positive_refund_line_qty"),)


class RefundPayment(db.Model):
    """Money paid back out. Kept separate from Payment (which is always money IN) rather than
    reusing it with a negative amount, so Payment's 'amount > 0' invariant never has exceptions."""

    __tablename__ = "refund_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    refund_id: Mapped[int] = mapped_column(ForeignKey("refunds.id", ondelete="CASCADE"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    cash_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("cash_register_sessions.id", ondelete="SET NULL")
    )
    method: Mapped[str] = mapped_column(String(20))
    amount: Mapped[object] = mapped_column(MONEY)
    reference: Mapped[str | None] = mapped_column(String(120))
    paid_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    refund: Mapped[Refund] = relationship(back_populates="payments")

    __table_args__ = (
        CheckConstraint("method in " + str(REFUND_PAYMENT_METHODS),
                        name="valid_refund_payment_method"),
        CheckConstraint("amount > 0", name="positive_refund_payment"),
    )


CUSTOMER_LEDGER_ENTRY_TYPES = ("sale", "payment", "refund", "adjustment")


class CustomerBalance(db.Model):
    """Cached balance a customer owes (credit sales). Always re-derivable from the ledger."""

    __tablename__ = "customer_balances"

    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"),
                                              primary_key=True)
    balance: Mapped[object] = mapped_column(MONEY, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class CustomerLedgerEntry(db.Model):
    """Append-only, same pattern as supplier_ledger_entries."""

    __tablename__ = "customer_ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"),
                                             index=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    entry_type: Mapped[str] = mapped_column(String(12))
    amount: Mapped[object] = mapped_column(MONEY)  # signed: +sale on credit, -payment/-refund
    previous_balance: Mapped[object] = mapped_column(MONEY)
    new_balance: Mapped[object] = mapped_column(MONEY)
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    customer: Mapped[Customer] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("entry_type in " + str(CUSTOMER_LEDGER_ENTRY_TYPES),
                        name="valid_customer_ledger_entry_type"),
    )


class CustomerPayment(TimestampMixin, db.Model):
    __tablename__ = "customer_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    amount: Mapped[object] = mapped_column(MONEY)
    method: Mapped[str] = mapped_column(String(20), default="cash", server_default="cash")
    reference: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(String(255))
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    customer: Mapped[Customer] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("amount > 0", name="positive_customer_payment"),)


LOYALTY_ENTRY_TYPES = ("earn", "redeem", "adjustment")


class LoyaltyTransaction(db.Model):
    """Append-only, same pattern as the other ledgers."""

    __tablename__ = "loyalty_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"),
                                             index=True)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    entry_type: Mapped[str] = mapped_column(String(12))
    points: Mapped[int] = mapped_column(Integer)  # signed: +earn, -redeem
    balance_after: Mapped[int] = mapped_column(Integer)
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    customer: Mapped[Customer] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("entry_type in " + str(LOYALTY_ENTRY_TYPES), name="valid_loyalty_entry_type"),
        CheckConstraint("balance_after >= 0", name="loyalty_balance_nonneg"),
    )


RESERVATION_STATUSES = ("requested", "confirmed", "seated", "completed", "cancelled", "no_show")


class Reservation(TimestampMixin, db.Model):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    guest_name: Mapped[str | None] = mapped_column(String(120))  # used when no Customer record
    guest_phone: Mapped[str | None] = mapped_column(String(40))
    party_size: Mapped[int] = mapped_column(Integer)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    table_id: Mapped[int | None] = mapped_column(ForeignKey("tables.id", ondelete="SET NULL"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(12), default="requested", server_default="requested")
    notes: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    branch = relationship("Branch")
    customer: Mapped[Customer | None] = relationship(lazy="joined")
    table: Mapped[Table | None] = relationship(lazy="joined")
    order: Mapped[Order | None] = relationship()

    __table_args__ = (
        CheckConstraint("party_size > 0", name="positive_party_size"),
        CheckConstraint("status in " + str(RESERVATION_STATUSES), name="valid_reservation_status"),
        Index("ix_reservations_branch_time", "branch_id", "reserved_at"),
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
