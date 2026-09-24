"""Production entry point:  waitress-serve --call wsgi:app   (or gunicorn on Linux)."""
from app import create_app

app = create_app()
