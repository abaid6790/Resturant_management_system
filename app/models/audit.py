from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class AuditLog(db.Model):
    """Append-only. A database trigger rejects UPDATE and DELETE (see migration 0002)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    username: Mapped[str | None] = mapped_column(String(32))  # snapshot, survives renames
    action: Mapped[str] = mapped_column(String(64))
    module: Mapped[str] = mapped_column(String(48))
    record_type: Mapped[str | None] = mapped_column(String(64))
    record_id: Mapped[str | None] = mapped_column(String(64))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    reference: Mapped[str | None] = mapped_column(String(120))
    ip: Mapped[str | None] = mapped_column(String(45))
    request_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_audit_logs_module_created", "module", "created_at"),
        Index("ix_audit_logs_record", "record_type", "record_id"),
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_branch_id", "branch_id"),
    )
