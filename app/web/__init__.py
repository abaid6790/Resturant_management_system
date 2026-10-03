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
        kitchen,
        locations,
        orders,
        purchases,
        recipes,
        refunds,
        reservations,
        roles,
        settings,
        stock,
        suppliers,
        users,
    )

    for module in (auth, home, users, roles, branches, settings, audit, categories, items,
                  locations, stock, recipes, suppliers, purchases, floors, customers, orders, cash,
                  kitchen, refunds, reservations):
        app.register_blueprint(module.bp)
