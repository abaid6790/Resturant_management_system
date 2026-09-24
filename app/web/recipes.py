from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import ValidationError
from app.core.http import paginate
from app.extensions import db
from app.models.inventory import InventoryItem, Recipe
from app.services import catalog as catalog_svc
from app.services import recipes as svc

bp = Blueprint("recipes", __name__, url_prefix="/inventory/recipes")


@bp.get("/")
@require("recipes.view")
def index():
    q = request.args.get("q", "")
    stmt = (db.select(InventoryItem).join(Recipe, Recipe.item_id == InventoryItem.id)
           .where(InventoryItem.is_active.is_(True)).order_by(InventoryItem.name))
    if q:
        stmt = stmt.where(InventoryItem.name.ilike(f"%{q}%"))
    items, meta = paginate(stmt)
    without = list(db.session.scalars(
        db.select(InventoryItem)
        .where(InventoryItem.is_active.is_(True),
              InventoryItem.item_type.in_(("finished_product", "semi_finished")),
              ~InventoryItem.id.in_(db.select(Recipe.item_id)))
        .order_by(InventoryItem.name)
    ))
    return render_template("recipes/list.html", items=items, meta=meta, q=q, without=without)


def _location_for_costing():
    return g.branch and next(iter(catalog_svc.visible_locations(g.user, g.branch.id, False)), None) \
        or next(iter(catalog_svc.visible_locations(g.user, include_inactive=False)), None)


@bp.route("/<int:item_id>", methods=["GET", "POST"])
@require("recipes.view")
def edit(item_id):
    item = catalog_svc.get_item_or_404(item_id)
    recipe = db.session.scalar(db.select(Recipe).where(Recipe.item_id == item.id))
    errors = {}
    lines = ([{"item_id": ln.ingredient_item_id, "quantity": str(ln.quantity), "unit_id": ln.unit_id,
              "name": ln.ingredient.name, "unit_code": ln.unit.code} for ln in recipe.lines]
            if recipe else [])
    yield_qty = str(recipe.yield_qty) if recipe else "1"

    if request.method == "POST":
        if not g.user.has_permission("recipes.manage"):
            from app.core.errors import PermissionDeniedError
            raise PermissionDeniedError()
        f = request.form
        raw_lines = [{"item_id": i, "quantity": q, "unit_id": u}
                    for i, q, u in zip(f.getlist("ing_item_id"), f.getlist("ing_qty"),
                                       f.getlist("ing_unit_id"), strict=False) if i]
        try:
            svc.save(g.user, item, {"yield_qty": f.get("yield_qty", "1"), "lines": raw_lines})
            db.session.commit()
            flash("Recipe saved.", "success")
            return redirect(url_for("recipes.edit", item_id=item.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
            yield_qty = f.get("yield_qty", "1")
            lines = []
            for i, q, u in zip(f.getlist("ing_item_id"), f.getlist("ing_qty"),
                               f.getlist("ing_unit_id"), strict=False):
                if not i:
                    continue
                ing = db.session.get(InventoryItem, int(i))
                unit = db.session.get(catalog_svc.UnitOfMeasure, int(u)) if u.isdigit() else None
                lines.append({"item_id": i, "quantity": q, "unit_id": u,
                             "name": ing.name if ing else "?", "unit_code": unit.code if unit else "?"})

    costing = None
    if recipe:
        loc = _location_for_costing()
        if loc:
            costing = svc.costing_summary(recipe, loc)
    ingredient_choices = list(db.session.scalars(
        db.select(InventoryItem).where(InventoryItem.is_active.is_(True)).order_by(InventoryItem.name)
    ))
    status = 422 if errors else 200
    return render_template("recipes/form.html", item=item, recipe=recipe, lines=lines,
                           yield_qty=yield_qty, errors=errors, costing=costing,
                           ingredients=ingredient_choices, units=catalog_svc.all_units(),
                           can_manage=g.user.has_permission("recipes.manage")), status
