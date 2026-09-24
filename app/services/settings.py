from __future__ import annotations

from flask import g, has_request_context

from app.core.errors import ValidationError
from app.core.settings_registry import BY_KEY, REGISTRY, coerce
from app.extensions import db
from app.models.settings import Setting
from app.services import audit


def _stored() -> dict:
    """All stored overrides, cached for the duration of one request."""
    if has_request_context():
        if not hasattr(g, "_settings_cache"):
            g._settings_cache = {s.key: s.value for s in db.session.scalars(db.select(Setting))}
        return g._settings_cache
    return {s.key: s.value for s in db.session.scalars(db.select(Setting))}


def get(key: str):
    defn = BY_KEY[key]
    return _stored().get(key, defn.default)


def all_values() -> dict:
    stored = _stored()
    return {d.key: stored.get(d.key, d.default) for d in REGISTRY}


def update(raw: dict, actor, keys: list[str] | None = None) -> dict:
    """Validate and save. All-or-nothing: any invalid field aborts with per-field errors."""
    current, errors, clean = all_values(), {}, {}
    for d in REGISTRY:
        if keys is not None and d.key not in keys:
            continue
        try:
            clean[d.key] = coerce(d, raw.get(d.key) if d.kind != "bool" else raw.get(d.key))
        except ValueError as exc:
            errors[d.key] = str(exc)
    if errors:
        raise ValidationError("Some settings are invalid.", details=errors)

    changed = {k: v for k, v in clean.items() if current.get(k) != v}
    for k, v in changed.items():
        row = db.session.get(Setting, k)
        if row:
            row.value, row.updated_by = v, actor.id
        else:
            db.session.add(Setting(key=k, value=v, updated_by=actor.id))
    if changed:
        audit.log("settings.update", "settings", record_type="settings",
                  before={k: current[k] for k in changed}, after=changed)
        db.session.flush()
        if has_request_context() and hasattr(g, "_settings_cache"):
            del g._settings_cache
    return changed
