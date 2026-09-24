from flask import Flask


def register_web(app: Flask) -> None:
    from . import (
        audit,
        auth,
        branches,
        categories,
        home,
        items,
        locations,
        purchases,
        recipes,
        roles,
        settings,
        stock,
        suppliers,
        users,
    )

    for module in (auth, home, users, roles, branches, settings, audit, categories, items,
                  locations, stock, recipes, suppliers, purchases):
        app.register_blueprint(module.bp)
