from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import can_assign_role, require
from app.core.errors import ValidationError
from app.core.permissions import CATALOG
from app.extensions import db
from app.services import roles as role_svc

bp = Blueprint("roles", __name__, url_prefix="/admin/roles")


def _ctx(role, form, errors):
    return dict(role=role, form=form, errors=errors, catalog=CATALOG,
                mine=g.user.permission_codes, locked=bool(role and role.is_super),
                readonly=bool(role and not can_assign_role(g.user, role) and not g.user.is_super))


def _read() -> dict:
    return {"name": request.form.get("name", ""), "description": request.form.get("description", ""),
            "permissions": request.form.getlist("permissions")}


@bp.get("/")
@require("roles.manage")
def index():
    return render_template("roles/list.html", rows=role_svc.list_roles())


@bp.route("/new", methods=["GET", "POST"])
@require("roles.manage")
def new():
    form, errors = {"permissions": []}, {}
    if request.method == "POST":
        form = _read()
        try:
            role_svc.create(g.user, form)
            db.session.commit()
            flash("Role created.", "success")
            return redirect(url_for("roles.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("roles/form.html", **_ctx(None, form, errors)), (422 if errors else 200)


@bp.route("/<int:role_id>/edit", methods=["GET", "POST"])
@require("roles.manage")
def edit(role_id):
    role = role_svc.get_or_404(role_id)
    errors = {}
    form = {"name": role.name, "description": role.description or "",
            "permissions": sorted(p.code for p in role.permissions)}
    if request.method == "POST":
        form = _read()
        try:
            role_svc.update(g.user, role, form)
            db.session.commit()
            flash("Role saved. The change applies to signed-in users immediately.", "success")
            return redirect(url_for("roles.index"))
        except ValidationError as err:
            db.session.rollback()
            errors = err.details or {}
            flash(err.message, "error")
    return render_template("roles/form.html", **_ctx(role, form, errors)), (422 if errors else 200)


@bp.post("/<int:role_id>/delete")
@require("roles.manage")
def delete(role_id):
    role = role_svc.get_or_404(role_id)
    role_svc.delete(g.user, role)
    db.session.commit()
    flash(f"Deleted role {role.name}.", "success")
    return redirect(url_for("roles.index"))
