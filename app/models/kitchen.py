from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from .mixins import TimestampMixin

QTY = db.Numeric(16, 4)

TICKET_STATUSES = ("queued", "preparing", "ready", "served", "cancelled")
TICKET_PRIORITIES = ("normal", "rush")
NEXT_STATUS = {"queued": "preparing", "preparing": "ready", "ready": "served"}


class KitchenStation(TimestampMixin, db.Model):
    __tablename__ = "kitchen_stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    branch = relationship("Branch")

    __table_args__ = (UniqueConstraint("branch_id", "name", name="uq_station_branch_name"),)


class KitchenTicket(TimestampMixin, db.Model):
    """
    One KOT: the items from a single 'fire' event that belong to one station (or None, for
    items with no station assigned). An order can have several tickets — one per station, and a
    new set each time more items are fired (e.g. starters fired first, mains fired later).
    """

    __tablename__ = "kitchen_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_number: Mapped[str] = mapped_column(String(30), unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    station_id: Mapped[int | None] = mapped_column(ForeignKey("kitchen_stations.id", ondelete="SET NULL"))
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(12), default="queued", server_default="queued")
    priority: Mapped[str] = mapped_column(String(10), default="normal", server_default="normal")
    fired_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order = relationship("Order")
    station: Mapped[KitchenStation | None] = relationship(lazy="joined")
    lines: Mapped[list[KitchenTicketLine]] = relationship(back_populates="ticket",
                                                          cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("status in " + str(TICKET_STATUSES), name="valid_ticket_status"),
        CheckConstraint("priority in " + str(TICKET_PRIORITIES), name="valid_ticket_priority"),
        Index("ix_kitchen_tickets_branch_status", "branch_id", "status"),
        Index("ix_kitchen_tickets_station_status", "station_id", "status"),
    )


class KitchenTicketLine(db.Model):
    __tablename__ = "kitchen_ticket_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("kitchen_tickets.id", ondelete="CASCADE"))
    order_line_id: Mapped[int] = mapped_column(ForeignKey("order_lines.id", ondelete="CASCADE"),
                                               unique=True)
    quantity: Mapped[object] = mapped_column(QTY)
    notes: Mapped[str | None] = mapped_column(String(255))

    ticket: Mapped[KitchenTicket] = relationship(back_populates="lines")
    order_line = relationship("OrderLine")
