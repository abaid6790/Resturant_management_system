import re

from app.core import security
from app.extensions import db
from app.models.audit import AuditLog
from app.models.auth import Branch, Permission, Role, User
from app.services import bootstrap

PASSWORD = "Str0ng-pass!"


def seed(app) -> dict:
    with app.app_context():
        bootstrap.seed_roles()
        a, b = Branch(code="A", name="Branch A"), Branch(code="B", name="Branch B")
        db.session.add_all([a, b])
        db.session.commit()
        return {"A": a.id, "B": b.id}


def make_role(app, name: str, perms: list[str]) -> int:
    with app.app_context():
        r = Role(name=name, is_system=False)
        r.permissions = list(db.session.scalars(db.select(Permission).where(Permission.code.in_(perms))))
        db.session.add(r)
        db.session.commit()
        return r.id


def make_user(app, username, role="Cashier", *, branches=(), all_branches=False, password=PASSWORD,
              must_change=False, active=True) -> int:
    with app.app_context():
        r = db.session.scalar(db.select(Role).where(Role.name == role))
        u = User(username=username, full_name=username.title(), role=r, all_branches=all_branches,
                 is_active=active, must_change_password=must_change,
                 password_hash=security.hash_password(password))
        u.branches = [db.session.get(Branch, i) for i in branches]
        db.session.add(u)
        db.session.commit()
        return u.id


def csrf(client) -> str:
    html = client.get("/login", follow_redirects=True).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def login(app, username, password=PASSWORD):
    c = app.test_client()
    r = c.post("/login", data={"username": username, "password": password, "csrf_token": csrf(c)})
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    return c


def post(client, url, data=None, **kw):
    d = dict(data or {})
    d["csrf_token"] = csrf(client)
    return client.post(url, data=d, **kw)


def audit_actions(app, **filters) -> list[str]:
    with app.app_context():
        stmt = db.select(AuditLog.action).order_by(AuditLog.id)
        for k, v in filters.items():
            stmt = stmt.where(getattr(AuditLog, k) == v)
        return list(db.session.scalars(stmt))


def user_row(app, username) -> User:
    with app.app_context():
        u = db.session.scalar(db.select(User).where(User.username == username))
        db.session.expunge(u)
        return u
