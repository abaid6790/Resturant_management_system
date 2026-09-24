from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.core.authz import allowed_branch_ids, require
from app.core.errors import ValidationError
from app.core.http import paginate
from app.extensions import db
from app.models.auth import Role
from app.services import branches as branch_svc
from app.services import roles as role_svc
from app.services import users as user_svc

bp = Blueprint("users", __name__, url_prefix="/admin/users")


def _form_ctx(user=None, form=None, errors=None):
    return dict(
        user=user, form=form or {}, errors=errors or {},
        roles=role_svc.assignable_roles(g.user), branches=branch_svc.visible_branches(g.user),
        can_all_branches=allowed_branch_ids(g.user) is None,
        min_length=user_svc.min_password_length(),
    )


def _read_form() -> dict:
    f = request.form
    return {"username": f.get("username", ""), "full_name": f.get("full_name", ""),
            "email": f.get("email", ""), "role_id": f.get("role_id", ""),
            "all_branches": f.get("all_branches") == "on", "branch_ids": f.getlist("branch_ids"),
            "password": f.get("password", ""), "must_change_password": f.get("must_change_password") == "on"}


@bp.get("/")
@require("users.view")
def index():
    q, status = request.args.get("q", ""), request.args.get("status", "")
    role_id = request.args.get("role_id", type=int)
    users, meta = paginate(user_svc.search(g.user, q, status, role_id))
    all_roles = db.session.scalars(db.select(Role).order_by(Role.name)).all()
    return render_template("users/list.html", users=users, meta=meta, q=q, status=status,
                           role_id=role_id, all_roles=all_roles)


@bp.route("/new", methods=["GET", "POST"])
@require("users.manage")
def new():
    if request.method == "POST":
        data = _read_form()
        try:
            u = user_svc.create(g.user, data)
            db.session.commit()
            flash(f"Created {u.full_name}. They must change the temporary password at first sign-in.",
                  "success")
            return redirect(url_for("users.index"))
        except ValidationError as err:
            db.session.rollback()
            flash(err.message, "error")
            return render_template("users/form.html", **_form_ctx(None, data, err.details)), 422
    return render_template("users/form.html", **_form_ctx(None, {"must_change_password": True}))


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@require("users.manage")
def edit(user_id):
    u = user_svc.get_or_404(g.user, user_id)
    if request.method == "POST":
        data = _read_form()
        try:
            user_svc.update(g.user, u, data)
            db.session.commit()
            flash("Changes saved.", "success")
            return redirect(url_for("users.index"))
        except ValidationError as err:
            db.session.rollback()
            flash(err.message, "error")
            return render_template("users/form.html", **_form_ctx(u, data, err.details)), 422
    form = {"username": u.username, "full_name": u.full_name, "email": u.email or "",
            "role_id": str(u.role_id), "all_branches": u.all_branches,
            "branch_ids": [str(b.id) for b in u.branches]}
    return render_template("users/form.html", **_form_ctx(u, form))


@bp.post("/<int:user_id>/status")
@require("users.manage")
def status(user_id):
    u = user_svc.get_or_404(g.user, user_id)
    active = request.form.get("active") == "1"
    user_svc.set_active(g.user, u, active)
    db.session.commit()
    flash(f"{u.full_name} is now {'active' if active else 'inactive'}.", "success")
    return redirect(url_for("users.index"))


@bp.post("/<int:user_id>/reset-password")
@require("users.manage")
def reset_password(user_id):
    u = user_svc.get_or_404(g.user, user_id)
    try:
        user_svc.reset_password(g.user, u, request.form.get("password", ""))
        db.session.commit()
        flash(f"Password reset for {u.full_name}. They must choose a new one at next sign-in.", "success")
    except ValidationError as err:
        db.session.rollback()
        flash((err.details or {}).get("password") or err.message, "error")
    return redirect(url_for("users.edit", user_id=u.id))


@bp.post("/<int:user_id>/unlock")
@require("users.manage")
def unlock(user_id):
    u = user_svc.get_or_404(g.user, user_id)
    user_svc.unlock(g.user, u)
    db.session.commit()
    flash(f"{u.full_name} can sign in again.", "success")
    return redirect(url_for("users.edit", user_id=u.id))
