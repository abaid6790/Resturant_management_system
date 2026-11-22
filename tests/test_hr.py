"""Employees, attendance, and shifts."""
import datetime as dt

from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.extensions import db
from app.services import hr as svc


def _actor(app):
    from app.models.auth import User
    u = db.session.scalar(db.select(User).where(User.username == "actor"))
    if u is None:
        from tests.helpers import make_user
        make_user(app, "actor", "Super Admin", all_branches=True)
        u = db.session.scalar(db.select(User).where(User.username == "actor"))
    return u


def _emp(app, ids, branch="A", name="Sam"):
    return svc.create(_actor(app), {"branch_id": str(ids[branch]), "full_name": name})


def test_employee_crud(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        e = svc.create(_actor(app), {"branch_id": str(ids["A"]), "full_name": "Maria Lopez",
                                     "position": "Waiter"})
        db.session.commit()
        assert e.is_active is True
        svc.update(_actor(app), e, {"full_name": "Maria L.", "position": "Head Waiter"})
        db.session.commit()
        assert e.position == "Head Waiter"
        svc.set_active(_actor(app), e, False)
        db.session.commit()
        assert e.is_active is False


def test_employee_requires_name_and_valid_email(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        for bad, key in (({"full_name": ""}, "full_name"),
                         ({"full_name": "X", "email": "not-an-email"}, "email")):
            try:
                svc.create(_actor(app), {"branch_id": str(ids["A"]), **bad})
                raise AssertionError
            except ValidationError as e:
                assert key in e.details


def test_user_can_only_be_linked_to_one_employee(finance):
    app, ids, x, p, f = finance
    from tests.helpers import make_user
    uid = make_user(app, "linked", "Waiter", branches=[ids["A"]])
    with app.app_context():
        svc.create(_actor(app), {"branch_id": str(ids["A"]), "full_name": "First", "user_id": str(uid)})
        db.session.commit()
        try:
            svc.create(_actor(app), {"branch_id": str(ids["A"]), "full_name": "Second",
                                     "user_id": str(uid)})
            raise AssertionError
        except ValidationError as e:
            assert "user_id" in e.details


def test_clock_in_and_out(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        e = _emp(app, ids)
        db.session.commit()
        assert svc.open_attendance_for(e.id) is None
        rec = svc.clock_in(_actor(app), e)
        db.session.commit()
        assert svc.open_attendance_for(e.id).id == rec.id
        for fn, arg in ((svc.clock_in, e),):
            try:
                fn(_actor(app), arg)
                raise AssertionError
            except BusinessRuleError:
                pass
        svc.clock_out(_actor(app), rec)
        db.session.commit()
        assert rec.clock_out is not None and svc.open_attendance_for(e.id) is None
        try:
            svc.clock_out(_actor(app), rec)
            raise AssertionError
        except BusinessRuleError:
            pass


def test_shift_lifecycle_and_duplicate_name_rejected(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        s = svc.save_shift(_actor(app), {"branch_id": str(ids["A"]), "name": "Morning",
                                         "start_time": "08:00", "end_time": "16:00"})
        db.session.commit()
        try:
            svc.save_shift(_actor(app), {"branch_id": str(ids["A"]), "name": "morning",
                                         "start_time": "09:00", "end_time": "17:00"})
            raise AssertionError
        except ValidationError:
            pass
        svc.set_shift_active(_actor(app), s, False)
        db.session.commit()
        assert s.is_active is False


def test_shift_assignment_rules(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        shift = svc.save_shift(_actor(app), {"branch_id": str(ids["A"]), "name": "Morning",
                                             "start_time": "08:00", "end_time": "16:00"})
        emp_a, emp_b = _emp(app, ids, "A", "A"), _emp(app, ids, "B", "B")
        db.session.commit()
        today = dt.date.today()
        try:
            svc.assign(_actor(app), shift, emp_b, today)  # wrong branch
            raise AssertionError
        except ValidationError as e:
            assert "employee_id" in e.details
        a = svc.assign(_actor(app), shift, emp_a, today)
        db.session.commit()
        assert a.status == "scheduled"
        try:
            svc.assign(_actor(app), shift, emp_a, today)  # duplicate
            raise AssertionError
        except ValidationError as e:
            assert "work_date" in e.details
        svc.set_assignment_status(_actor(app), a, "completed")
        db.session.commit()
        assert a.status == "completed"


def test_assign_accepts_an_iso_date_string_and_rejects_garbage(finance):
    """Regression: the web form posts work_date as a string; comparing it raw to a DATE column
    used to crash with a database type error (a 500)."""
    app, ids, x, p, f = finance
    with app.app_context():
        shift = svc.save_shift(_actor(app), {"branch_id": str(ids["A"]), "name": "Evening",
                                             "start_time": "16:00", "end_time": "23:00"})
        emp = _emp(app, ids)
        db.session.commit()
        a = svc.assign(_actor(app), shift, emp, dt.date.today().isoformat())
        db.session.commit()
        assert a.work_date == dt.date.today()
        try:
            svc.assign(_actor(app), shift, emp, "not-a-date")
            raise AssertionError
        except ValidationError as e:
            assert "work_date" in e.details


def test_service_date_filters_tolerate_strings_and_garbage(finance):
    app, ids, x, p, f = finance
    with app.app_context():
        today = dt.date.today().isoformat()
        for stmt in (svc.attendance_stmt(_actor(app), date_from=today, date_to=today),
                     svc.attendance_stmt(_actor(app), date_from="garbage", date_to=""),
                     svc.assignments_stmt(_actor(app), date_from=today, date_to=today),
                     svc.assignments_stmt(_actor(app), date_from="garbage")):
            list(db.session.scalars(stmt))


def test_employee_branch_scoping(finance):
    app, ids, x, p, f = finance
    from tests.helpers import make_user
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    with app.app_context():
        from app.models.auth import User
        e_b = _emp(app, ids, "B", "Other Branch")
        db.session.commit()
        mgr = db.session.scalar(db.select(User).where(User.username == "mgr"))
        try:
            svc.get_or_404(mgr, e_b.id)
            raise AssertionError
        except NotFoundError:
            pass
