# gunicorn.conf.py — equivalente a:
# gunicorn --bind 0.0.0.0:${INVOICE_PORT} --workers 2 --timeout 90 app.wsgi:app
# Solo se usa dentro del contenedor Linux (Gunicorn no funciona en Windows).
from app.config import Settings  # importable gracias a PYTHONPATH=/app

_settings = Settings.from_env()  # valida INVOICE_PORT; ConfigError → Gunicorn termina ≠ 0
bind = f"{_settings.host}:{_settings.port}"  # 0.0.0.0:INVOICE_PORT (Req. 10.6, 12.1, 12.2)
workers = 2
worker_class = "sync"
timeout = 90  # > ocr_timeout_seconds (60 s)
graceful_timeout = 30
preload_app = True  # arranque (11.6, 11.7) una vez, antes del fork
accesslog = "-"
errorlog = "-"
