from __future__ import annotations

from app.core.authz import can_assign_role
from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError, ValidationError
from app.core.permissions import ALL_CODES
from app.extensions import db
from app.models.auth import Permission, Role, User
from app.services import audit


def list_roles() -> list[tuple[Role, int]]:
    counts = dict(db.session.execute(
        db.select(User.role_id, db.func.count(User.id)).group_by(User.role_id)).all())
    roles = db.session.scalars(db.select(Role).order_by(Role.is_super.desc(), Role.name))
    return [(r, counts.get(r.id, 0)) for r in roles]


def get_or_404(role_id: int) -> Role:
    r = db.session.get(Role, role_id)
    if r is None:
        raise NotFoundError("Role not found.")
    return r


def assignable_roles(actor) -> list[Role]:
    return [r for r in db.session.scalars(db.select(Role).order_by(Role.name))
            if can_assign_role(actor, r)]


def _snap(role: Role) -> dict:
    return {"name": role.name, "description": role.description,
            "permissions": sorted(p.code for p in role.permissions)}


def _clean(data: dict, existing: Role | None) -> tuple[str, str | None, set[str]]:
    errors = {}
    name = (data.get("name") or "").strip()
    if existing is not None and existing.is_system:
        name = existing.name  # system role names are fixed
    if not 2 <= len(name) <= 60:
        errors["name"] = "Enter a role name (2–60 characters)."
    else:
        clash = db.session.scalar(db.select(Role).where(db.func.lower(Role.name) == name.lower()))
        if clash and (existing is None or clash.id != existing.id):
            errors["name"] = "Another role already has this name."
    desc = (data.get("description") or "").strip()[:255] or None
    codes = set(data.get("permissions") or [])
    if codes - ALL_CODES:
        errors["permissions"] = "Unknown permission selected."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    return name, desc, codes


def _guard_grant(actor, new_codes: set[str], old_codes: set[str]) -> None:
    added = new_codes - old_codes
    if not actor.is_super and not added <= actor.permission_codes:
        raise PermissionDeniedError("You cannot grant permissions that you do not have yourself.")


def _apply(role: Role, codes: set[str]) -> None:
    perms = db.session.scalars(db.select(Permission).where(Permission.code.in_(codes))).all()
    role.permissions = list(perms)


def create(actor, data: dict) -> Role:
    name, desc, codes = _clean(data, None)
    _guard_grant(actor, codes, set())
    role = Role(name=name, description=desc, is_system=False, is_super=False)
    db.session.add(role)
    _apply(role, codes)
    db.session.flush()
    audit.log("role.create", "roles", record_type="role", record_id=role.id, after=_snap(role))
    return role


def update(actor, role: Role, data: dict) -> Role:
    if role.is_super:
        raise BusinessRuleError("The Super Admin role always has every permission and cannot be edited.")
    if not can_assign_role(actor, role) and not actor.is_super:
        raise PermissionDeniedError("You cannot edit a role that has more access than you do.")
    name, desc, codes = _clean(data, role)
    before = _snap(role)
    _guard_grant(actor, codes, set(before["permissions"]))
    role.name, role.description = name, desc
    _apply(role, codes)
    db.session.flush()
    audit.log_change("role.update", "roles", before, _snap(role), record_type="role",
                     record_id=role.id)
    return role


def delete(actor, role: Role) -> None:
    if role.is_system:
        raise BusinessRuleError("System roles cannot be deleted.")
    if db.session.scalar(db.select(db.func.count(User.id)).where(User.role_id == role.id)):
        raise BusinessRuleError("This role is assigned to users. Move them to another role first.")
    audit.log("role.delete", "roles", record_type="role", record_id=role.id, before=_snap(role))
    db.session.delete(role)
