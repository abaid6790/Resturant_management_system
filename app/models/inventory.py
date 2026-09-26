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
RATE = Numeric(9, 4)


class UnitOfMeasure(db.Model):
    __tablename__ = "units_of_measure"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)
    name: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(10))  # weight | volume | count
    factor_to_base: Mapped[object] = mapped_column(Numeric(18, 6))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    __table_args__ = (CheckConstraint("factor_to_base > 0", name="positive_factor"),)


class InventoryCategory(TimestampMixin, db.Model):
    __tablename__ = "inventory_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


ITEM_TYPES = ("raw_material", "ingredient", "semi_finished", "finished_product", "packaging")


class InventoryItem(TimestampMixin, db.Model):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    item_type: Mapped[str] = mapped_column(String(20))
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_categories.id", ondelete="RESTRICT")
    )
    stock_unit_id: Mapped[int] = mapped_column(ForeignKey("units_of_measure.id", ondelete="RESTRICT"))
    track_batches: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    min_stock: Mapped[object] = mapped_column(QTY, default=0, server_default="0")
    max_stock: Mapped[object | None] = mapped_column(QTY)
    reorder_level: Mapped[object] = mapped_column(QTY, default=0, server_default="0")
    selling_price: Mapped[object | None] = mapped_column(MONEY)  # for finished/semi-finished items
    tax_rate_pct: Mapped[object | None] = mapped_column(RATE)  # overrides settings.tax.rate_pct
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    description: Mapped[str | None] = mapped_column(Text)

    category: Mapped[InventoryCategory | None] = relationship(lazy="joined")
    stock_unit: Mapped[UnitOfMeasure] = relationship(lazy="joined")
    packaging_units: Mapped[list[ItemPackagingUnit]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="ItemPackagingUnit.name"
    )

    __table_args__ = (
        CheckConstraint("item_type in ('raw_material','ingredient','semi_finished',"
                        "'finished_product','packaging')", name="valid_item_type"),
        CheckConstraint("min_stock >= 0", name="min_stock_nonneg"),
        CheckConstraint("reorder_level >= 0", name="reorder_nonneg"),
        Index("ix_inventory_items_category_id", "category_id"),
    )


class ItemPackagingUnit(db.Model):
    """A convenience unit specific to one item, e.g. 'Box' = 24 of the item's stock unit."""

    __tablename__ = "item_packaging_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(40))
    factor_to_stock_unit: Mapped[object] = mapped_column(Numeric(16, 4))

    item: Mapped[InventoryItem] = relationship(back_populates="packaging_units")

    __table_args__ = (
        UniqueConstraint("item_id", "name", name="uq_item_packaging_name"),
        CheckConstraint("factor_to_stock_unit > 0", name="positive_packaging_factor"),
    )


class Location(TimestampMixin, db.Model):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(80))
    location_type: Mapped[str] = mapped_column(String(20), default="store", server_default="store")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    branch = relationship("Branch")

    __table_args__ = (
        UniqueConstraint("branch_id", "name", name="uq_location_branch_name"),
        CheckConstraint("location_type in ('warehouse','kitchen','store','bar')",
                        name="valid_location_type"),
    )


class StockBalance(db.Model):
    """Cached current balance per item/location. Always re-derivable by summing stock_movements."""

    __tablename__ = "stock_balances"

    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"),
                                         primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"),
                                              primary_key=True)
    qty: Mapped[object] = mapped_column(QTY, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    item: Mapped[InventoryItem] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")


class StockBatch(TimestampMixin, db.Model):
    """A FIFO cost layer. qty_remaining reaches 0 as the batch is consumed; never negative."""

    __tablename__ = "stock_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"), index=True)
    batch_no: Mapped[str] = mapped_column(String(60))
    unit_cost: Mapped[object] = mapped_column(COST)
    qty_received: Mapped[object] = mapped_column(QTY)
    qty_remaining: Mapped[object] = mapped_column(QTY)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expiry_date: Mapped[date | None] = mapped_column(Date)
    source_type: Mapped[str] = mapped_column(String(30))  # opening | adjustment | purchase | transfer
    source_id: Mapped[int | None] = mapped_column(Integer)

    item: Mapped[InventoryItem] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("qty_remaining >= 0", name="batch_remaining_nonneg"),
        CheckConstraint("qty_remaining <= qty_received", name="batch_remaining_le_received"),
        Index("ix_stock_batches_fifo", "item_id", "location_id", "received_at"),
    )


MOVEMENT_TYPES = ("opening", "adjustment_in", "adjustment_out", "transfer_in", "transfer_out",
                  "consumption", "wastage", "purchase_in", "purchase_return")


class StockMovement(db.Model):
    """
    Append-only ledger (see migration: DB trigger blocks UPDATE/DELETE). Every inventory change,
    whatever its cause, creates exactly one of these with a full explanation of the balance.
    """

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"), index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"), index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("stock_batches.id", ondelete="RESTRICT"))
    movement_type: Mapped[str] = mapped_column(String(20))
    qty: Mapped[object] = mapped_column(QTY)  # signed: +in, -out
    unit_cost: Mapped[object] = mapped_column(COST)
    previous_balance: Mapped[object] = mapped_column(QTY)
    new_balance: Mapped[object] = mapped_column(QTY)
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))

    item: Mapped[InventoryItem] = relationship(lazy="joined")
    location: Mapped[Location] = relationship(lazy="joined")
    user = relationship("User")

    __table_args__ = (
        CheckConstraint("movement_type in " + str(MOVEMENT_TYPES), name="valid_movement_type"),
        # new_balance is NOT constrained to >= 0 at the DB level: going negative is a deliberate,
        # permission-gated override (settings.inventory.allow_negative_stock), enforced in
        # app/services/inventory.py, not a hard data-integrity invariant like batch quantities are.
        Index("ix_stock_movements_item_loc", "item_id", "location_id", "created_at"),
    )


class Recipe(TimestampMixin, db.Model):
    """A BOM: how much of each ingredient is needed to produce ONE stock unit of the output item."""

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="CASCADE"), unique=True)
    yield_qty: Mapped[object] = mapped_column(QTY, default=1, server_default="1")
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    item: Mapped[InventoryItem] = relationship(lazy="joined")
    lines: Mapped[list[RecipeLine]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="RecipeLine.id"
    )

    __table_args__ = (CheckConstraint("yield_qty > 0", name="positive_yield"),)


class RecipeLine(db.Model):
    __tablename__ = "recipe_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    ingredient_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"))
    quantity: Mapped[object] = mapped_column(QTY)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units_of_measure.id", ondelete="RESTRICT"))
    notes: Mapped[str | None] = mapped_column(String(255))

    recipe: Mapped[Recipe] = relationship(back_populates="lines")
    ingredient: Mapped[InventoryItem] = relationship(lazy="joined")
    unit: Mapped[UnitOfMeasure] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_recipe_qty"),
        UniqueConstraint("recipe_id", "ingredient_item_id", name="uq_recipe_ingredient"),
    )
