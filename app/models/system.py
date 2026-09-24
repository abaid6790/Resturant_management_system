from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

from .mixins import TimestampMixin


class SchemaMeta(TimestampMixin, db.Model):
    """Small key/value table for system facts (e.g. install id). Proves the migration pipeline."""

    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
