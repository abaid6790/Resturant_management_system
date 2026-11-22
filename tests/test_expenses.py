"""Expenses: creation, validation, and the cash-register interaction."""
import datetime as dt

from app.core.errors import ValidationError
from app.core.money import D
from app.extensions import db
from app.models.expenses import Expense
from app.services import cash as cash_svc
from app.services import expenses as svc


def _actor(app):
    from app.models.auth import User
    u = db.session.scalar(db.select(User).where(User.username == "actor"))
    if u is None:
        from tests.helpers import make_user
        make_user(app, "actor", "Super Admin", all_branches=True)
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
    return u


def _exp(ids, f, **kw):
    return {"branch_id": str(ids["A"]), "category_id": str(f["category"]), "method": "card", **kw}


def test_category_lifecycle(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        c = svc.save_category(_actor(app), {"name": "Rent"})
        db.session.commit()
        try:
            svc.save_category(_actor(app), {"name": "rent"})
            raise AssertionError
        except ValidationError:
            pass
        svc.set_category_active(_actor(app), c, False)
        db.session.commit()
        assert c.is_active is False


def test_create_expense_validates_fields(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        try:
            svc.create(_actor(app), _exp(ids, f, amount="0"))
            raise AssertionError
        except ValidationError as e:
            assert "amount" in e.details
        try:
            svc.create(_actor(app), {**_exp(ids, f, amount="10.00"), "category_id": "999999"})
            raise AssertionError
        except ValidationError as e:
            assert "category_id" in e.details


def test_non_cash_expense_does_not_touch_cash_register(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        e = svc.create(_actor(app), _exp(ids, f, amount="50.00"))
        db.session.commit()
        assert e.cash_session_id is None


def test_cash_expense_debits_the_open_register(finance):
    app, ids, x, p, f = finance
    from app.models.inventory import Location
    with app.app_context():
        session = cash_svc.open_session(_actor(app), db.session.get(Location, x["loc_a"]), "100.00")
        db.session.commit()
        e = svc.create(_actor(app), _exp(ids, f, amount="30.00", method="cash"))
        db.session.commit()
        assert e.cash_session_id == session.id
        assert cash_svc.session_cash_total(session.id) == D("70.00")


def test_cash_expense_without_open_register_just_records_the_expense(finance):
    """An expense doesn't need an open drawer to exist as a record (unlike a sale or refund)."""
    app, ids, x, p, f = finance
    with app.app_context():
        e = svc.create(_actor(app), _exp(ids, f, amount="15.00", method="cash"))
        db.session.commit()
        assert e.cash_session_id is None
        assert db.session.get(Expense, e.id).amount == D("15.00")


def test_total_and_by_category_for_period(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        today = dt.date.today()
        for amt in ("20.00", "30.00"):
            svc.create(_actor(app), _exp(ids, f, amount=amt, expense_date=today.isoformat()))
        db.session.commit()
        assert svc.total_for_period(ids["A"], today, today) == D("50.00")
        assert svc.by_category_for_period(_actor(app), ids["A"], today, today) == [("Utilities", D("50.00"))]


def test_expense_requires_branch_access(finance):
    app, ids, x, p, f = finance
    from tests.helpers import make_user
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    with app.app_context():
        from app.models.auth import User
        mgr = db.session.scalar(db.select(User).where(User.username == "mgr"))
        try:
            svc.create(mgr, {**_exp(ids, f, amount="10.00"), "branch_id": str(ids["B"])})
            raise AssertionError
        except ValidationError as e:
            assert "branch_id" in e.details


def test_service_date_filter_tolerates_strings_and_garbage(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        today = dt.date.today().isoformat()
        for stmt in (svc.visible_stmt(_actor(app), date_from=today, date_to=today),
                     svc.visible_stmt(_actor(app), date_from="garbage", date_to="")):
            list(db.session.scalars(stmt))  # must run, not raise a database type error
