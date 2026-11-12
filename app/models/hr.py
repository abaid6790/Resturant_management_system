from __future__ import annotations

from datetime import date as date_
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from .mixins import TimestampMixin

SHIFT_ASSIGNMENT_STATUSES = ("scheduled", "completed", "missed", "cancelled")


class Employee(TimestampMixin, db.Model):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"),
                                                unique=True)
    full_name: Mapped[str] = mapped_column(String(120))
    position: Mapped[str | None] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    hire_date: Mapped[date_ | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    branch = relationship("Branch")
    user = relationship("User")


class AttendanceRecord(TimestampMixin, db.Model):
    __tablename__ = "attendance_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"),
                                             index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    clock_in: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clock_out: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(255))
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    employee: Mapped[Employee] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("clock_out is null or clock_out > clock_in", name="clock_out_after_in"),
        Index("ix_attendance_employee_clockin", "employee_id", "clock_in"),
    )


class Shift(TimestampMixin, db.Model):
    """A named time-of-day pattern (e.g. 'Morning', 8:00-16:00) that assignments are built from."""

    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(60))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    branch = relationship("Branch")

    __table_args__ = (UniqueConstraint("branch_id", "name", name="uq_shift_branch_name"),)


class ShiftAssignment(TimestampMixin, db.Model):
    __tablename__ = "shift_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id", ondelete="RESTRICT"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"),
                                             index=True)
    work_date: Mapped[date_] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="scheduled", server_default="scheduled")
    notes: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    shift: Mapped[Shift] = relationship(lazy="joined")
    employee: Mapped[Employee] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("status in " + str(SHIFT_ASSIGNMENT_STATUSES), name="valid_assignment_status"),
        UniqueConstraint("shift_id", "employee_id", "work_date", name="uq_assignment_shift_employee_date"),
        Index("ix_assignments_employee_date", "employee_id", "work_date"),
    )
