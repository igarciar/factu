# Invoice Reader — imagen de producción (Req. 10.1–10.4, 10.6, 13.4)
FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Tesseract + idioma español (Req. 10.2) + Ollama (vision model OCR fallback)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa \
 && rm -rf /var/lib/apt/lists/*

# Ollama binary installation from GitHub release (optional; if not in repos)
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && curl -L https://github.com/ollama/ollama/releases/download/v0.4.48/ollama-linux-amd64 -o /usr/local/bin/ollama \
 && chmod +x /usr/local/bin/ollama \
 && apt-get remove -y curl \
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
COPY app/entrypoint.sh ./

USER appuser

ENV INVOICE_PORT=8000
EXPOSE 8000 11434

# Solo biblioteca estándar (no hace falta curl); 503 o error de conexión → exit 1 (Req. 13.4)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"INVOICE_PORT\",\"8000\")}/health',timeout=2).status==200 else 1)"

# entrypoint.sh starts Ollama, waits for it, pre-pulls the model, and then starts Gunicorn
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "app.wsgi:app"]
