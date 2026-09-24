"""
Recipe (BOM) costing and consumption. A recipe line's quantity is stated in whatever unit is
convenient (grams, ml, pieces); it is converted to the ingredient's own stock unit before costing
or consuming, so g/kg/ml/l mismatches between a recipe and its ingredient never cause silent errors.

Semi-finished items (sauces, dough...) are modelled as recipes on their own item: a recipe line can
point at another item that itself has a recipe, and cost_of_recipe() recurses through it. This is
the Phase 2 default in place of separate manufacturing/production orders (see docs/DECISIONS.md).
"""
from __future__ import annotations

from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.money import D, money
from app.core.money import qty as qround
from app.core.money import unit_cost as cround
from app.core.units import convert
from app.extensions import db
from app.models.inventory import InventoryItem, Recipe, RecipeLine
from app.services import audit
from app.services.inventory import consume_stock, current_unit_cost

ZERO = D(0)


def get_or_404(item_id: int) -> Recipe:
    r = db.session.scalar(db.select(Recipe).where(Recipe.item_id == item_id))
    if r is None:
        raise NotFoundError("This item has no recipe yet.")
    return r


def requirements_for(recipe: Recipe, produce_qty):
    """[(ingredient_item, qty_in_ingredient_stock_unit), ...] to make `produce_qty` stock units."""
    factor = D(produce_qty) / D(recipe.yield_qty)
    out = []
    for line in recipe.lines:
        needed = D(line.quantity) * factor
        converted = convert(needed, line.unit, line.ingredient.stock_unit)
        out.append((line.ingredient, converted))
    return out


def cost_of_recipe(recipe: Recipe, location, *, _seen: frozenset[int] = frozenset()):
    """
    Theoretical cost to produce ONE stock unit, using each ingredient's current on-hand cost.
    Recurses into sub-recipes (semi-finished ingredients); guards against circular BOMs. Kept at
    full unit-cost precision (not rounded to money) so a sub-cent per-gram cost doesn't get
    rounded away before it is multiplied back up through a parent recipe; round only for display
    (see costing_summary).
    """
    if recipe.item_id in _seen:
        raise BusinessRuleError(f"Circular recipe: {recipe.item.name} refers back to itself.")
    total = ZERO
    for ingredient, qty in requirements_for(recipe, recipe.yield_qty):
        sub = db.session.scalar(db.select(Recipe).where(Recipe.item_id == ingredient.id,
                                                        Recipe.is_active.is_(True)))
        unit_cost = (cost_of_recipe(sub, location, _seen=_seen | {recipe.item_id})
                    if sub else current_unit_cost(ingredient, location))
        total += D(unit_cost) * qty
    return cround(total / D(recipe.yield_qty))


def costing_summary(recipe: Recipe, location) -> dict:
    cost = money(cost_of_recipe(recipe, location))  # rounded to money here, for display only
    price = recipe.item.selling_price
    out = {"unit_cost": cost, "selling_price": price}
    if price and D(price) > 0:
        out["gross_profit"] = money(D(price) - cost)
        out["gross_margin_pct"] = money((D(price) - cost) / D(price) * 100)
        out["food_cost_pct"] = money(cost / D(price) * 100)
    return out


def consume_for_output(recipe: Recipe, location, produce_qty, actor, *, reference_type=None,
                       reference_id=None, reason=None):
    """
    Deduct every ingredient this recipe needs to produce `produce_qty` output units, at `location`.
    Recurses through sub-recipes: a semi-finished ingredient with its own recipe and insufficient
    stock is itself produced on the fly (its own ingredients are consumed instead).
    All-or-nothing: the caller's transaction rolls back everything if any ingredient runs short.
    """
    movements = []
    for ingredient, needed in requirements_for(recipe, produce_qty):
        from app.services.inventory import current_balance
        sub = db.session.scalar(db.select(Recipe).where(Recipe.item_id == ingredient.id,
                                                        Recipe.is_active.is_(True)))
        if sub and current_balance(ingredient.id, location.id) < needed:
            movements += consume_for_output(sub, location, needed, actor,
                                            reference_type=reference_type,
                                            reference_id=reference_id, reason=reason)
        else:
            movements += consume_stock(item=ingredient, location=location, quantity=needed,
                                      actor=actor, movement_type="consumption",
                                      reference_type=reference_type, reference_id=reference_id,
                                      reason=reason or f"Recipe: {recipe.item.name}")
    return movements


def _snap(recipe: Recipe) -> dict:
    return {"yield_qty": str(recipe.yield_qty), "is_active": recipe.is_active,
            "lines": sorted((ln.ingredient.sku, str(ln.quantity), ln.unit.code) for ln in recipe.lines)}


def save(actor, item: InventoryItem, data: dict) -> Recipe:
    """Create or replace a recipe's lines. `data['lines']` = [{item_id, quantity, unit_id}, ...]."""
    errors = {}
    try:
        yield_qty = qround(data.get("yield_qty") or "1")
        if yield_qty <= 0:
            errors["yield_qty"] = "Enter a yield greater than zero."
    except Exception:  # noqa: BLE001
        errors["yield_qty"] = "Enter a valid number."
        yield_qty = D(1)

    raw_lines, seen_ids = data.get("lines") or [], set()
    lines = []
    for i, raw in enumerate(raw_lines):
        if not raw.get("item_id"):
            continue
        ing = db.session.get(InventoryItem, int(raw["item_id"]))
        if ing is None:
            errors[f"line{i}"] = "Unknown ingredient."
            continue
        if ing.id == item.id:
            errors[f"line{i}"] = "A recipe cannot use its own item as an ingredient."
            continue
        if ing.id in seen_ids:
            errors[f"line{i}"] = f"{ing.name} is listed twice."
            continue
        seen_ids.add(ing.id)
        try:
            q = qround(raw.get("quantity"))
            assert q > 0
        except Exception:  # noqa: BLE001
            errors[f"line{i}"] = "Enter a quantity greater than zero."
            continue
        unit = _unit(raw.get("unit_id"))
        if unit is None:
            errors[f"line{i}"] = "Choose a unit."
            continue
        if unit.kind != ing.stock_unit.kind:
            errors[f"line{i}"] = f"{ing.name} is tracked in {ing.stock_unit.kind}; choose a matching unit."
            continue
        lines.append(RecipeLine(ingredient_item_id=ing.id, quantity=q, unit_id=unit.id,
                                notes=(raw.get("notes") or "")[:255] or None))
    if not lines and not errors:
        errors["lines"] = "Add at least one ingredient."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", details=errors)

    recipe = db.session.scalar(db.select(Recipe).where(Recipe.item_id == item.id))
    before = _snap(recipe) if recipe else None
    if recipe is None:
        recipe = Recipe(item_id=item.id, yield_qty=yield_qty)
        db.session.add(recipe)
    else:
        recipe.yield_qty = yield_qty
        recipe.lines.clear()
        db.session.flush()
    recipe.lines = lines
    db.session.flush()
    after = _snap(recipe)
    if before is None:
        audit.log("recipe.create", "recipes", record_type="recipe", record_id=recipe.id, after=after)
    else:
        audit.log_change("recipe.update", "recipes", before, after, record_type="recipe",
                         record_id=recipe.id)
    return recipe


def _unit(unit_id):
    from app.models.inventory import UnitOfMeasure
    return db.session.get(UnitOfMeasure, int(unit_id)) if str(unit_id or "").isdigit() else None
