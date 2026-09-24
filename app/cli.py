"""Command line helpers:  flask seed | flask create-superadmin"""
import click
from flask import Flask
from flask.cli import with_appcontext

from app.extensions import db


def register_cli(app: Flask) -> None:
    @app.cli.command("seed")
    @with_appcontext
    def seed():
        """Create missing permissions and system roles (safe to run repeatedly)."""
        from app.services import bootstrap
        bootstrap.seed_roles()
        db.session.commit()
        click.echo("Permissions, system roles and standard units are up to date.")

    @app.cli.command("create-superadmin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.password_option()
    @with_appcontext
    def create_superadmin(username, full_name, password):
        """Create an additional Super Admin from the command line (e.g. account recovery)."""
        from app.core import security
        from app.models.auth import Role, User
        from app.services import bootstrap
        from app.services.auth import check_password, find_user
        bootstrap.seed_roles()
        if find_user(username):
            raise click.ClickException("That username already exists.")
        check_password(password, username)
        role = db.session.scalar(db.select(Role).where(Role.is_super.is_(True)))
        db.session.add(User(username=username.lower(), full_name=full_name, role=role,
                            all_branches=True, password_hash=security.hash_password(password)))
        db.session.commit()
        click.echo(f"Super Admin {username} created.")
