"""Units, categories, items and their item-specific packaging units."""
from __future__ import annotations

import re

from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.money import D, money
from app.core.money import qty as qround
from app.core.units import STANDARD_UNITS
from app.extensions import db
from app.models.inventory import (
    ITEM_TYPES,
    InventoryCategory,
    InventoryItem,
    ItemPackagingUnit,
    StockMovement,
    UnitOfMeasure,
)
from app.models.pos import ProductModifier
from app.services import audit

SKU_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{1,39}")


def seed_units() -> int:
    added = 0
    existing = {u.code for u in db.session.scalars(db.select(UnitOfMeasure))}
    for code, name, kind, factor in STANDARD_UNITS:
        if code not in existing:
            db.session.add(UnitOfMeasure(code=code, name=name, kind=kind, factor_to_base=factor))
            added += 1
    db.session.flush()
    return added


def all_units() -> list[UnitOfMeasure]:
    return list(db.session.scalars(db.select(UnitOfMeasure).where(UnitOfMeasure.is_active.is_(True))
                                   .order_by(UnitOfMeasure.kind, UnitOfMeasure.factor_to_base)))


# ---- categories ---------------------------------------------------------------------------------

def list_categories(include_inactive=True) -> list[InventoryCategory]:
    stmt = db.select(InventoryCategory).order_by(InventoryCategory.name)
    if not include_inactive:
        stmt = stmt.where(InventoryCategory.is_active.is_(True))
    return list(db.session.scalars(stmt))


def get_category_or_404(cid: int) -> InventoryCategory:
    c = db.session.get(InventoryCategory, cid)
    if c is None:
        raise NotFoundError("Category not found.")
    return c


def save_category(actor, data: dict, existing: InventoryCategory | None = None) -> InventoryCategory:
    name = (data.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValidationError("Enter a category name.", details={"name": "Required, up to 80 characters."})
    clash = db.session.scalar(db.select(InventoryCategory)
                              .where(db.func.lower(InventoryCategory.name) == name.lower()))
    if clash and (existing is None or clash.id != existing.id):
        raise ValidationError("Please fix the highlighted fields.",
                              details={"name": "Another category already has this name."})
    desc = (data.get("description") or "").strip()[:255] or None
    if existing is None:
        c = InventoryCategory(name=name, description=desc)
        db.session.add(c)
        db.session.flush()
        audit.log("category.create", "inventory", record_type="category", record_id=c.id,
                  after={"name": name})
        return c
    before = {"name": existing.name, "description": existing.description}
    existing.name, existing.description = name, desc
    audit.log_change("category.update", "inventory", before, {"name": name, "description": desc},
                     record_type="category", record_id=existing.id)
    return existing


def set_category_active(actor, c: InventoryCategory, active: bool) -> None:
    if c.is_active != active:
        c.is_active = active
        audit.log("category.activate" if active else "category.deactivate", "inventory",
                  record_type="category", record_id=c.id)


# ---- items ---------------------------------------------------------------------------------------

def search_items(q="", item_type="", category_id=None, active_only=True):
    stmt = db.select(InventoryItem).order_by(InventoryItem.name)
    if active_only:
        stmt = stmt.where(InventoryItem.is_active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(db.or_(InventoryItem.name.ilike(like), InventoryItem.sku.ilike(like)))
    if item_type:
        stmt = stmt.where(InventoryItem.item_type == item_type)
    if category_id:
        stmt = stmt.where(InventoryItem.category_id == category_id)
    return stmt


def get_item_or_404(item_id: int) -> InventoryItem:
    item = db.session.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("Item not found.")
    return item


def _clean_item(data: dict, existing: InventoryItem | None) -> dict:
    errors, out = {}, {}
    if existing is not None:
        out["sku"] = existing.sku  # immutable once created; the edit form never submits it
    else:
        sku = (data.get("sku") or "").strip().upper()
        if not SKU_RE.fullmatch(sku):
            errors["sku"] = "Use 2–40 letters, numbers, dots, dashes or underscores."
        elif db.session.scalar(db.select(InventoryItem).where(InventoryItem.sku == sku)):
            errors["sku"] = "Another item already uses this SKU."
        out["sku"] = sku
    out["name"] = (data.get("name") or "").strip()
    if not out["name"] or len(out["name"]) > 150:
        errors["name"] = "Enter an item name."
    if data.get("item_type") not in ITEM_TYPES:
        errors["item_type"] = "Choose an item type."
    out["item_type"] = data.get("item_type")
    cat_id = data.get("category_id")
    out["category_id"] = int(cat_id) if str(cat_id or "").isdigit() else None
    unit_id = data.get("stock_unit_id")
    unit = db.session.get(UnitOfMeasure, int(unit_id)) if str(unit_id or "").isdigit() else None
    if unit is None:
        errors["stock_unit_id"] = "Choose the unit this item is tracked in."
    out["stock_unit_id"] = unit.id if unit else None
    out["track_batches"] = bool(data.get("track_batches"))
    for f, label in (("min_stock", "Minimum stock"), ("reorder_level", "Reorder level")):
        try:
            out[f] = qround(data.get(f) or "0")
            if out[f] < 0:
                raise ValueError
        except Exception:  # noqa: BLE001
            errors[f] = f"{label} must be a number of zero or more."
    max_raw = (data.get("max_stock") or "").strip()
    if max_raw:
        try:
            out["max_stock"] = qround(max_raw)
        except Exception:  # noqa: BLE001
            errors["max_stock"] = "Enter a valid number, or leave blank."
    else:
        out["max_stock"] = None
    if "max_stock" not in errors and out.get("max_stock") is not None and "min_stock" not in errors:
        if out["max_stock"] < out["min_stock"]:
            errors["max_stock"] = "Maximum stock cannot be less than minimum stock."
    price_raw = (data.get("selling_price") or "").strip()
    if price_raw:
        try:
            out["selling_price"] = D(price_raw)
            if out["selling_price"] < 0:
                raise ValueError
        except Exception:  # noqa: BLE001
            errors["selling_price"] = "Enter a valid price, or leave blank."
    else:
        out["selling_price"] = None
    shelf_raw = (data.get("shelf_life_days") or "").strip()
    out["shelf_life_days"] = None
    if shelf_raw:
        if not shelf_raw.isdigit():
            errors["shelf_life_days"] = "Enter a whole number of days."
        else:
            out["shelf_life_days"] = int(shelf_raw)
    out["description"] = (data.get("description") or "").strip()[:2000] or None
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    return out


def _snap(item: InventoryItem) -> dict:
    return {"sku": item.sku, "name": item.name, "item_type": item.item_type,
            "category_id": item.category_id, "stock_unit_id": item.stock_unit_id,
            "track_batches": item.track_batches, "min_stock": str(item.min_stock),
            "max_stock": str(item.max_stock) if item.max_stock is not None else None,
            "reorder_level": str(item.reorder_level),
            "selling_price": str(item.selling_price) if item.selling_price is not None else None}


def create_item(actor, data: dict) -> InventoryItem:
    clean = _clean_item(data, None)
    item = InventoryItem(**clean, is_active=True)
    db.session.add(item)
    db.session.flush()
    audit.log("item.create", "inventory", record_type="item", record_id=item.id, after=_snap(item))
    return item


def update_item(actor, item: InventoryItem, data: dict) -> InventoryItem:
    clean = _clean_item(data, item)
    if item.track_batches and not clean["track_batches"]:
        has_stock = db.session.scalar(
            db.select(db.func.count()).select_from(StockMovement).where(StockMovement.item_id == item.id)
        )
        if has_stock:
            raise BusinessRuleError(
                "Batch tracking cannot be turned off for an item that already has stock movements."
            )
    before = _snap(item)
    for k, v in clean.items():
        setattr(item, k, v)
    audit.log_change("item.update", "inventory", before, _snap(item), record_type="item",
                     record_id=item.id)
    return item


def set_item_active(actor, item: InventoryItem, active: bool) -> None:
    if item.is_active != active:
        item.is_active = active
        audit.log("item.activate" if active else "item.deactivate", "inventory",
                  record_type="item", record_id=item.id)


def save_packaging_units(actor, item: InventoryItem, rows: list[dict]) -> None:
    errors, clean, seen = {}, [], set()
    for i, r in enumerate(rows):
        name = (r.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in seen:
            errors[f"pk{i}"] = f"“{name}” is listed twice."
            continue
        seen.add(name.lower())
        try:
            factor = qround(r.get("factor"))
            assert factor > 0
        except Exception:  # noqa: BLE001
            errors[f"pk{i}"] = f"Enter a positive quantity for “{name}”."
            continue
        clean.append(ItemPackagingUnit(item_id=item.id, name=name[:40], factor_to_stock_unit=factor))
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    item.packaging_units.clear()
    db.session.flush()
    item.packaging_units.extend(clean)


# ---- locations -------------------------------------------------------------------------------------

def visible_locations(actor, branch_id: int | None = None, include_inactive=True):
    from app.core.authz import allowed_branch_ids
    from app.models.inventory import Location
    stmt = db.select(Location).order_by(Location.name)
    ids = allowed_branch_ids(actor)
    if ids is not None:
        stmt = stmt.where(Location.branch_id.in_(ids))
    if branch_id:
        stmt = stmt.where(Location.branch_id == branch_id)
    if not include_inactive:
        stmt = stmt.where(Location.is_active.is_(True))
    return list(db.session.scalars(stmt))


def get_location_or_404(actor, location_id: int):
    from app.services.inventory import get_location_or_404 as _get
    return _get(actor, location_id)


def save_location(actor, data: dict, existing=None):
    from app.core.authz import allowed_branch_ids, ensure_branch_access
    from app.models.auth import Branch
    from app.models.inventory import Location
    errors = {}
    branch_id = int(data.get("branch_id")) if str(data.get("branch_id") or "").isdigit() else None
    if branch_id is None or db.session.get(Branch, branch_id) is None:
        errors["branch_id"] = "Choose a branch."
    elif allowed_branch_ids(actor) is not None:
        try:
            ensure_branch_access(branch_id, actor)
        except Exception:  # noqa: BLE001
            errors["branch_id"] = "You do not have access to that branch."
    name = (data.get("name") or "").strip()
    if not name or len(name) > 80:
        errors["name"] = "Enter a location name."
    ltype = data.get("location_type") or "store"
    if ltype not in ("warehouse", "kitchen", "store", "bar"):
        errors["location_type"] = "Choose a valid type."
    if "branch_id" not in errors and "name" not in errors:
        clash = db.session.scalar(db.select(Location).where(Location.branch_id == branch_id,
                                                             db.func.lower(Location.name) == name.lower()))
        if clash and (existing is None or clash.id != existing.id):
            errors["name"] = "This branch already has a location with that name."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    if existing is None:
        loc = Location(branch_id=branch_id, name=name, location_type=ltype, is_active=True)
        db.session.add(loc)
        db.session.flush()
        audit.log("location.create", "inventory", record_type="location", record_id=loc.id,
                  branch_id=branch_id, after={"name": name})
        return loc
    before = {"name": existing.name, "location_type": existing.location_type}
    existing.name, existing.location_type = name, ltype
    audit.log_change("location.update", "inventory", before, {"name": name, "location_type": ltype},
                     record_type="location", record_id=existing.id, branch_id=existing.branch_id)
    return existing


def set_location_active(actor, loc, active: bool) -> None:
    if loc.is_active != active:
        loc.is_active = active
        audit.log("location.activate" if active else "location.deactivate", "inventory",
                  record_type="location", record_id=loc.id, branch_id=loc.branch_id)


# ---- modifiers -------------------------------------------------------------------------------

def save_modifiers(actor, item: InventoryItem, rows: list[dict]) -> None:
    """Replace an item's optional add-ons, e.g. 'Extra cheese' (+1.00). Mirrors packaging units."""
    errors, clean, seen = {}, [], set()
    for i, r in enumerate(rows):
        name = (r.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in seen:
            errors[f"mod{i}"] = f"“{name}” is listed twice."
            continue
        seen.add(name.lower())
        try:
            delta = D(r.get("price_delta") or "0")
        except Exception:  # noqa: BLE001
            errors[f"mod{i}"] = f"Enter a valid price for “{name}”."
            continue
        clean.append(ProductModifier(item_id=item.id, name=name[:60], price_delta=money(delta)))
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)
    db.session.query(ProductModifier).filter(ProductModifier.item_id == item.id).delete()
    db.session.flush()
    for m in clean:
        db.session.add(m)
