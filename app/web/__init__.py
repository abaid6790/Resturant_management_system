from flask import Flask


def register_web(app: Flask) -> None:
    from . import (
        audit,
        auth,
        branches,
        cash,
        categories,
        customers,
        floors,
        home,
        items,
        locations,
        orders,
        purchases,
        recipes,
        roles,
        settings,
        stock,
        suppliers,
        users,
    )

    for module in (auth, home, users, roles, branches, settings, audit, categories, items,
                  locations, stock, recipes, suppliers, purchases, floors, customers, orders, cash):
        app.register_blueprint(module.bp)
