from __future__ import annotations

import re

from app.core.authz import allowed_branch_ids
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.extensions import db
from app.models.auth import Branch
from app.services import audit

FIELDS = ["code", "name", "address", "phone", "is_active"]


def visible_branches(actor, *, include_inactive: bool = True) -> list[Branch]:
    stmt = db.select(Branch).order_by(Branch.name)
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Branch.id.in_(ids))
    if not include_inactive:
        stmt = stmt.where(Branch.is_active.is_(True))
    return list(db.session.scalars(stmt))


def get_or_404(actor, branch_id: int) -> Branch:
    ids = allowed_branch_ids(actor)
    b = db.session.get(Branch, branch_id)
    if b is None or (ids is not None and b.id not in ids):
        raise NotFoundError("Branch not found.")
    return b


def _clean(data: dict, existing: Branch | None) -> dict:
    errors, out = {}, {}
    out["code"] = (data.get("code") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{2,20}", out["code"]):
        errors["code"] = "Use 2–20 letters, numbers, dashes or underscores."
    out["name"] = (data.get("name") or "").strip()
    if not out["name"] or len(out["name"]) > 120:
        errors["name"] = "Enter a branch name (up to 120 characters)."
    out["address"] = (data.get("address") or "").strip() or None
    out["phone"] = (data.get("phone") or "").strip()[:40] or None
    if "code" not in errors:
        clash = db.session.scalar(db.select(Branch).where(Branch.code == out["code"]))
        if clash and (existing is None or clash.id != existing.id):
            errors["code"] = "Another branch already uses this code."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    return out


def create(actor, data: dict) -> Branch:
    clean = _clean(data, None)
    b = Branch(**clean, is_active=True)
    db.session.add(b)
    db.session.flush()
    if not (actor.all_branches or actor.is_super):  # creator keeps access to what they create
        actor.branches.append(b)
    audit.log("branch.create", "branches", record_type="branch", record_id=b.id, branch_id=b.id,
              after=audit.snapshot(b, FIELDS))
    return b


def update(actor, b: Branch, data: dict) -> Branch:
    before = audit.snapshot(b, FIELDS)
    for k, v in _clean(data, b).items():
        setattr(b, k, v)
    audit.log_change("branch.update", "branches", before, audit.snapshot(b, FIELDS),
                     record_type="branch", record_id=b.id, branch_id=b.id)
    return b


def set_active(actor, b: Branch, active: bool) -> None:
    if b.is_active == active:
        return
    if not active:
        others = db.session.scalar(
            db.select(db.func.count(Branch.id)).where(Branch.is_active.is_(True), Branch.id != b.id)
        )
        if not others:
            raise BusinessRuleError("At least one branch must stay active.")
    before = audit.snapshot(b, FIELDS)
    b.is_active = active
    audit.log_change("branch.activate" if active else "branch.deactivate", "branches", before,
                     audit.snapshot(b, FIELDS), record_type="branch", record_id=b.id,
                     branch_id=b.id)
