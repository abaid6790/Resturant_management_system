"""Small date helpers shared by services."""
from __future__ import annotations

import datetime as dt


def as_date(value) -> dt.date | None:
    """Coerce an ISO date string (as submitted by a form or query string) to a date.

    Date and datetime objects pass through; blank or unparseable input returns None, which every
    filter in this codebase treats as "no filter on this end" rather than an error. Passing a raw
    string to a DATE comparison makes PostgreSQL raise a type error (a 500), so services convert
    at the boundary instead of trusting each caller to.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError:
        return None
