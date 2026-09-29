"""WSGI entry point for Gunicorn: ``gunicorn --config gunicorn.conf.py app.wsgi:app``.

Importing this module runs the full start-up. If it fails, ``load_app`` logs the affected
path and exits with code 1; with ``preload_app = True`` Gunicorn then stops before forking
any worker (Req. 10.6, 11.7).
"""

from app.main import load_app

app = load_app()
