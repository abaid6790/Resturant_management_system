from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import require
from app.core.errors import ValidationError
from app.core.http import paginate
from app.extensions import db
from app.models.inventory import ITEM_TYPES
from app.services import catalog as svc
from app.services import kitchen as kitchen_svc
from app.services.inventory import current_unit_cost

bp = Blueprint("items", __name__, url_prefix="/inventory/items")


@bp.get("/")
@require("products.view")
def index():
    q, item_type = request.args.get("q", ""), request.args.get("item_type", "")
    category_id = request.args.get("category_id", type=int)
    items, meta = paginate(svc.search_items(q, item_type, category_id))
    return render_template("items/list.html", items=items, meta=meta, q=q, item_type=item_type,
                           category_id=category_id, item_types=ITEM_TYPES,
                           categories=svc.list_categories(include_inactive=False))


def _packaging_rows():
    names, factors = request.form.getlist("pkg_name"), request.form.getlist("pkg_factor")
    return [{"name": n, "factor": f} for n, f in zip(names, factors, strict=False)]


def _form(existing=None):
    form, errors = {"item_type": "ingredient", "track_batches": True}, {}
    if existing:
        form = {"sku": existing.sku, "name": existing.name, "item_type": existing.item_type,
                "category_id": str(existing.category_id or ""), "stock_unit_id": str(existing.stock_unit_id),
                "track_batches": existing.track_batches, "min_stock": str(existing.min_stock),
                "max_stock": str(existing.max_stock) if existing.max_stock is not None else "",
                "reorder_level": str(existing.reorder_level),
                "selling_price": str(existing.selling_price) if existing.selling_price is not None else "",
                "shelf_life_days": str(existing.shelf_life_days or ""),
                "description": existing.description or ""}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            item = svc.update_item(g.user, existing, form) if existing else svc.create_item(g.user, form)
            svc.save_packaging_units(g.user, item, _packaging_rows())
            db.session.commit()
            flash("Item saved." if existing else "Item created.", "success")
            return redirect(url_for("items.edit", item_id=item.id))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
            form["_packaging"] = _packaging_rows()
    status = 422 if errors else 200
    return render_template("items/form.html", item=existing, form=form, errors=errors,
                           item_types=ITEM_TYPES, categories=svc.list_categories(include_inactive=False),
                           units=svc.all_units(), stations=kitchen_svc.visible_stations(
                               g.user, include_inactive=False)), status


@bp.route("/new", methods=["GET", "POST"])
@require("products.manage")
def new():
    return _form()


@bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@require("products.manage")
def edit(item_id):
    item = svc.get_item_or_404(item_id)
    resp = _form(item)
    return resp


@bp.post("/<int:item_id>/status")
@require("products.manage")
def status(item_id):
    item = svc.get_item_or_404(item_id)
    svc.set_item_active(g.user, item, request.form.get("active") == "1")
    db.session.commit()
    return redirect(url_for("items.index"))


@bp.get("/<int:item_id>")
@require("inventory.view")
def show(item_id):
    from app.services import catalog as catsvc
    item = svc.get_item_or_404(item_id)
    locations = catsvc.visible_locations(g.user, include_inactive=False)
    balances = {loc.id: {"balance": None, "cost": None} for loc in locations}
    from app.services.inventory import current_balance
    for loc in locations:
        bal = current_balance(item.id, loc.id)
        balances[loc.id] = {"balance": bal, "cost": current_unit_cost(item, loc) if bal else None}
    return render_template("items/show.html", item=item, locations=locations, balances=balances)
