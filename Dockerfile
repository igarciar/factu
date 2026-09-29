# Invoice Reader — imagen de producción (Req. 10.1–10.4, 10.6, 13.4)
FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Tesseract + idioma español (Req. 10.2)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa \
 && rm -rf /var/lib/apt/lists/*

# Usuario sin privilegios uid/gid 10001 y directorios de datos (Req. 10.3)
RUN groupadd --system --gid 10001 appuser \
 && useradd --system --uid 10001 --gid 10001 --home /app --no-create-home appuser \
 && mkdir -p /data/db /data/images \
 && chown -R appuser:appuser /data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gunicorn.conf.py ./
COPY app ./app

USER appuser

ENV INVOICE_PORT=8000
EXPOSE 8000

# Solo biblioteca estándar (no hace falta curl); 503 o error de conexión → exit 1 (Req. 13.4)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"INVOICE_PORT\",\"8000\")}/health',timeout=2).status==200 else 1)"

# Gunicorn en forma exec (PID 1); bind 0.0.0.0:INVOICE_PORT en gunicorn.conf.py (Req. 10.6)
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "app.wsgi:app"]
