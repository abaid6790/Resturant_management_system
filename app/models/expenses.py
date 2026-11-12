from __future__ import annotations

from datetime import date as date_

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from .mixins import TimestampMixin

MONEY = db.Numeric(14, 2)
EXPENSE_PAYMENT_METHODS = ("cash", "card", "bank_transfer", "mobile_wallet", "other")


class ExpenseCategory(TimestampMixin, db.Model):
    __tablename__ = "expense_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Expense(TimestampMixin, db.Model):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    category_id: Mapped[int] = mapped_column(ForeignKey("expense_categories.id", ondelete="RESTRICT"))
    amount: Mapped[object] = mapped_column(MONEY)
    expense_date: Mapped[date_] = mapped_column(Date, server_default=func.current_date())
    method: Mapped[str] = mapped_column(String(20), default="cash", server_default="cash")
    description: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(120))
    cash_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("cash_register_sessions.id", ondelete="SET NULL")
    )
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    branch = relationship("Branch")
    category: Mapped[ExpenseCategory] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("amount > 0", name="positive_expense_amount"),
        CheckConstraint("method in " + str(EXPENSE_PAYMENT_METHODS), name="valid_expense_method"),
        Index("ix_expenses_branch_date", "branch_id", "expense_date"),
    )
