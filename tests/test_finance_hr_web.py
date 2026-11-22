"""Web-layer tests for Phase 7: expenses, employees/attendance/shifts, and reports."""
import datetime as dt

from app.extensions import db
from app.models.expenses import Expense, ExpenseCategory
from app.models.hr import AttendanceRecord, Employee, Shift, ShiftAssignment
from tests.helpers import login, make_role, make_user, post


def test_expense_flow_through_http(finance):
    app, ids, x, p, f = finance
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    assert post(c, "/finance/expenses/new", {"branch_id": ids["A"], "category_id": f["category"],
                "amount": "45.00", "method": "card", "description": "Internet bill"}).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Expense)).amount == 45
    assert "45.00" in c.get("/finance/expenses/").get_data(as_text=True)
    assert post(c, "/finance/expenses/categories/new", {"name": "Marketing"}).status_code == 302
    with app.app_context():
        cat_id = db.session.scalar(db.select(ExpenseCategory.id).where(ExpenseCategory.name == "Marketing"))
    assert post(c, f"/finance/expenses/categories/{cat_id}/status", {"active": "0"}).status_code == 302


def test_expense_requires_manage_permission(finance):
    app, ids, x, p, f = finance
    make_role(app, "View Only Finance", ["expenses.view", "dashboard.view"])
    make_user(app, "vo", "View Only Finance", branches=[ids["A"]])
    c = login(app, "vo")
    assert c.get("/finance/expenses/").status_code == 200
    assert c.get("/finance/expenses/new").status_code == 403


def test_employee_and_attendance_flow_through_http(finance):
    app, ids, x, p, f = finance
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    assert post(c, "/hr/employees/new", {"branch_id": ids["A"], "full_name": "Maria Lopez",
                "position": "Waiter"}).status_code == 302
    with app.app_context():
        emp_id = db.session.scalar(db.select(Employee.id))
    assert "Clock in" in c.get(f"/hr/employees/{emp_id}").get_data(as_text=True)
    assert post(c, f"/hr/employees/{emp_id}/clock-in").status_code == 302
    with app.app_context():
        rec_id = db.session.scalar(db.select(AttendanceRecord.id))
    assert post(c, f"/hr/attendance/{rec_id}/clock-out").status_code == 302
    with app.app_context():
        assert db.session.get(AttendanceRecord, rec_id).clock_out is not None
    assert "Maria Lopez" in c.get("/hr/attendance").get_data(as_text=True)


def test_shift_creation_and_assignment_through_http(finance):
    """Includes the regression for the date-string bug: the form posts work_date as a string."""
    app, ids, x, p, f = finance
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    post(c, "/hr/employees/new", {"branch_id": ids["A"], "full_name": "Sam"})
    with app.app_context():
        emp_id = db.session.scalar(db.select(Employee.id))
    assert post(c, "/hr/shifts/new", {"branch_id": ids["A"], "name": "Morning",
                "start_time": "08:00", "end_time": "16:00"}).status_code == 302
    with app.app_context():
        shift_id = db.session.scalar(db.select(Shift.id))
    today = dt.date.today().isoformat()
    assert post(c, f"/hr/shifts/{shift_id}/assign",
                {"employee_id": emp_id, "work_date": today}).status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(ShiftAssignment.id))) == 1
    assert "Sam" in c.get(f"/hr/shifts?date={today}").get_data(as_text=True)


def test_reports_render_and_require_right_permission(finance):
    app, ids, x, p, f = finance
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    for url in ("/reports/profit-and-loss", "/reports/daily-closing", "/reports/employee-sales"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert "{{" not in r.get_data(as_text=True)
    make_role(app, "Ops Report", ["reports.view", "dashboard.view"])  # no reports.financial
    make_user(app, "ops", "Ops Report", branches=[ids["A"]])
    ops = login(app, "ops")
    assert ops.get("/reports/daily-closing").status_code == 200
    assert ops.get("/reports/profit-and-loss").status_code == 403


def test_hr_and_expenses_are_branch_scoped(finance):
    app, ids, x, p, f = finance
    make_user(app, "boss", "Owner", all_branches=True)
    make_user(app, "mgr", "Branch Manager", branches=[ids["A"]])
    post(login(app, "boss"), "/hr/employees/new", {"branch_id": ids["B"], "full_name": "Branch B Person"})
    with app.app_context():
        emp_b_id = db.session.scalar(db.select(Employee.id).where(Employee.branch_id == ids["B"]))
    mgr = login(app, "mgr")
    assert mgr.get(f"/hr/employees/{emp_b_id}").status_code == 404
    assert post(mgr, "/finance/expenses/new", {"branch_id": ids["B"], "category_id": f["category"],
                "amount": "5.00", "method": "card"}).status_code == 422


def test_date_filters_on_list_pages_do_not_crash(finance):
    """Regression: query-string dates reached SQL as raw strings and PostgreSQL rejected the
    comparison against DATE columns (a 500). Every filtered list must accept real dates, blanks
    and garbage."""
    app, ids, x, p, f = finance
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    today = dt.date.today().isoformat()
    for url in (f"/finance/expenses/?from={today}&to={today}", "/finance/expenses/?from=&to=",
                "/finance/expenses/?from=garbage", f"/hr/attendance?from={today}&to={today}",
                "/hr/attendance?from=garbage", f"/hr/shifts?date={today}", "/hr/shifts?date=garbage",
                "/hr/shifts?date=", f"/reports/profit-and-loss?from={today}&to={today}",
                "/reports/profit-and-loss?from=garbage", "/reports/daily-closing?date=garbage"):
        assert c.get(url).status_code == 200, url


def test_date_filters_actually_filter(finance):
    app, ids, x, p, f = finance
    make_user(app, "boss", "Owner", all_branches=True)
    c = login(app, "boss")
    today = dt.date.today()
    old = (today - dt.timedelta(days=30)).isoformat()
    post(c, "/finance/expenses/new", {"branch_id": ids["A"], "category_id": f["category"],
         "amount": "11.11", "method": "card", "expense_date": today.isoformat(),
         "description": "TODAY-EXPENSE-MARKER"})
    post(c, "/finance/expenses/new", {"branch_id": ids["A"], "category_id": f["category"],
         "amount": "22.22", "method": "card", "expense_date": old, "description": "OLD-EXPENSE-MARKER"})
    # match on the description: the "Recorded ..." flash toast echoes amounts, never descriptions
    html = c.get(f"/finance/expenses/?from={today.isoformat()}&to={today.isoformat()}").get_data(as_text=True)
    assert "TODAY-EXPENSE-MARKER" in html and "OLD-EXPENSE-MARKER" not in html
