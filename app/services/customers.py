from __future__ import annotations

from app.core.errors import NotFoundError, ValidationError
from app.extensions import db
from app.models.pos import Customer
from app.services import audit


def search(q="", active_only=True):
    stmt = db.select(Customer).order_by(Customer.name)
    if active_only:
        stmt = stmt.where(Customer.is_active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(db.or_(Customer.name.ilike(like), Customer.phone.ilike(like),
                                 Customer.email.ilike(like)))
    return stmt


def get_or_404(customer_id: int) -> Customer:
    c = db.session.get(Customer, customer_id)
    if c is None:
        raise NotFoundError("Customer not found.")
    return c


def _clean(data: dict) -> dict:
    errors, out = {}, {}
    out["name"] = (data.get("name") or "").strip()
    if not out["name"] or len(out["name"]) > 120:
        errors["name"] = "Enter the customer's name."
    out["phone"] = (data.get("phone") or "").strip()[:40] or None
    email = (data.get("email") or "").strip()
    if email and ("@" not in email or len(email) > 255):
        errors["email"] = "Enter a valid email address."
    out["email"] = email or None
    out["notes"] = (data.get("notes") or "").strip() or None
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    return out


def create(actor, data: dict) -> Customer:
    clean = _clean(data)
    c = Customer(**clean, is_active=True)
    db.session.add(c)
    db.session.flush()
    audit.log("customer.create", "pos", record_type="customer", record_id=c.id,
             after={"name": c.name})
    return c


def update(actor, c: Customer, data: dict) -> Customer:
    for k, v in _clean(data).items():
        setattr(c, k, v)
    audit.log("customer.update", "pos", record_type="customer", record_id=c.id,
             after={"name": c.name})
    return c


def set_active(actor, c: Customer, active: bool) -> None:
    if c.is_active != active:
        c.is_active = active
        audit.log("customer.activate" if active else "customer.deactivate", "pos",
                  record_type="customer", record_id=c.id)
