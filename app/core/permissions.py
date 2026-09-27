"""
Permission catalogue for the WHOLE system (all phases), so roles are designed once.
(code, label, sensitive). Sensitive permissions are flagged in the UI and are never granted
to a role by someone who does not hold them themselves (no privilege escalation).
"""
from __future__ import annotations

CATALOG: dict[str, list[tuple[str, str, bool]]] = {
    "Dashboard": [("dashboard.view", "View the dashboard", False)],
    "POS and orders": [
        ("pos.sell", "Take orders and payments", False),
        ("pos.hold", "Hold and resume orders", False),
        ("pos.discount", "Apply discounts within the limit", False),
        ("pos.discount_large", "Apply discounts above the limit", True),
        ("pos.price_override", "Override item prices", True),
        ("pos.void", "Void or cancel orders", True),
        ("pos.refund", "Refund and return sales", True),
        ("pos.transfer", "Transfer, merge and split tables and bills", False),
        ("pos.reprint", "Reprint invoices and receipts", False),
        ("orders.view_all", "View orders taken by other staff", False),
    ],
    "Tables and reservations": [
        ("tables.view", "View floors and tables", False),
        ("tables.manage", "Manage floors and tables", False),
        ("reservations.view", "View reservations", False),
        ("reservations.manage", "Create and change reservations", False),
    ],
    "Kitchen": [
        ("kitchen.view", "View the kitchen display", False),
        ("kitchen.update", "Change order preparation status", False),
        ("kitchen.manage", "Manage kitchen stations", False),
    ],
    "Menu and recipes": [
        ("products.view", "View products", False),
        ("products.manage", "Create and edit products", False),
        ("recipes.view", "View recipes and costs", False),
        ("recipes.manage", "Create and edit recipes", False),
        ("prices.change", "Change selling prices", True),
    ],
    "Inventory": [
        ("inventory.view", "View stock", False),
        ("inventory.view_costs", "View stock costs and valuation", False),
        ("inventory.manage", "Manage inventory items and locations", False),
        ("inventory.adjust", "Make stock adjustments", True),
        ("inventory.transfer", "Transfer stock between locations and branches", False),
        ("inventory.wastage", "Record wastage and spoilage", False),
    ],
    "Purchasing": [
        ("suppliers.view", "View suppliers", False),
        ("suppliers.manage", "Manage suppliers", False),
        ("purchases.view", "View purchases", False),
        ("purchases.create", "Create purchase orders", False),
        ("purchases.approve", "Approve purchase orders", True),
        ("purchases.receive", "Receive purchased stock", False),
        ("purchases.pay", "Record supplier payments", True),
        ("purchases.return", "Return purchased stock", False),
    ],
    "Customers": [
        ("customers.view", "View customers", False),
        ("customers.manage", "Manage customers", False),
        ("customers.credit", "Sell on credit and record customer payments", True),
        ("loyalty.manage", "Manage loyalty rules and adjust points", False),
    ],
    "Finance": [
        ("expenses.view", "View expenses", False),
        ("expenses.manage", "Record and edit expenses", False),
        ("cash.session", "Open and close own cash register session", False),
        ("cash.manage", "Manage all registers, cash in/out and closings", False),
        ("finance.adjust", "Make financial adjustments", True),
        ("reports.view", "View operational reports", False),
        ("reports.financial", "View profit and financial reports", True),
        ("reports.export", "Export and print reports", False),
    ],
    "Staff": [
        ("employees.view", "View employees", False),
        ("employees.manage", "Manage employees", False),
        ("attendance.view", "View attendance", False),
        ("attendance.manage", "Manage attendance", False),
        ("shifts.manage", "Manage shifts and assignments", False),
    ],
    "Administration": [
        ("users.view", "View users", False),
        ("users.manage", "Create and manage users", True),
        ("roles.manage", "Manage roles and permissions", True),
        ("branches.manage", "Manage branches", True),
        ("settings.view", "View settings", False),
        ("settings.manage", "Change settings", True),
        ("audit.view", "View the audit log", False),
        ("backup.manage", "Create and verify backups", False),
        ("backup.restore", "Restore backups", True),
    ],
}

ALL_CODES: frozenset[str] = frozenset(c for items in CATALOG.values() for c, _, _ in items)
SENSITIVE: frozenset[str] = frozenset(c for items in CATALOG.values() for c, _, s in items if s)


def _matching(*prefixes: str, exclude: tuple[str, ...] = ()) -> list[str]:
    return sorted(
        c for c in ALL_CODES
        if any(c == p or c.startswith(p) for p in prefixes) and c not in exclude
    )


# System roles created on first setup. Editable afterwards (except Super Admin).
DEFAULT_ROLES: dict[str, dict] = {
    "Super Admin": {"description": "Full access to everything, including all branches.",
                    "is_super": True, "permissions": []},
    "Owner": {"description": "Full business access.", "is_super": False,
              "permissions": sorted(ALL_CODES)},
    "Branch Manager": {
        "description": "Runs a branch: operations, stock, purchasing, staff and reports.",
        "is_super": False,
        "permissions": _matching(
            "dashboard.", "pos.", "orders.", "tables.", "reservations.", "kitchen.", "products.view",
            "recipes.view", "inventory.", "suppliers.", "purchases.", "customers.", "loyalty.",
            "expenses.", "cash.", "reports.view", "reports.export", "employees.", "attendance.",
            "shifts.", "users.view", "audit.view",
            exclude=("purchases.pay",),
        ),
    },
    "Cashier": {
        "description": "Takes orders and payments and runs their own register.",
        "is_super": False,
        "permissions": ["dashboard.view", "pos.sell", "pos.hold", "pos.discount", "pos.transfer",
                        "pos.reprint", "tables.view", "reservations.view", "reservations.manage",
                        "customers.view", "customers.manage", "cash.session", "kitchen.view"],
    },
    "Waiter": {
        "description": "Takes dine-in orders and manages their tables.",
        "is_super": False,
        "permissions": ["pos.sell", "pos.hold", "pos.transfer", "tables.view",
                        "reservations.view", "customers.view", "kitchen.view"],
    },
    "Kitchen Staff": {
        "description": "Uses the kitchen display.", "is_super": False,
        "permissions": ["kitchen.view", "kitchen.update"],
    },
    "Inventory Manager": {
        "description": "Manages stock, suppliers and purchasing.", "is_super": False,
        "permissions": _matching("dashboard.", "inventory.", "suppliers.", "purchases.",
                                 "products.view", "recipes.view", "reports.view",
                                 exclude=("purchases.pay", "purchases.approve")),
    },
    "Accountant": {
        "description": "Finance, expenses, ledgers and reports.", "is_super": False,
        "permissions": ["dashboard.view", "expenses.view", "expenses.manage", "cash.manage",
                        "reports.view", "reports.financial", "reports.export", "purchases.view",
                        "purchases.pay", "suppliers.view", "customers.view", "customers.credit",
                        "inventory.view", "inventory.view_costs", "audit.view"],
    },
}
