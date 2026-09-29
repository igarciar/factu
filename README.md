# Invoice Reader

Aplicación web para registrar facturas a partir de una foto. Subes la imagen (JPEG o PNG), la aplicación lee el texto con OCR (Tesseract, idioma español `spa`), propone los datos del apunte (fecha, proveedor, NIF/CIF, número, base, IVA, total) mediante reglas, tú los revisas y se guardan como gasto o ingreso junto con la foto original.

- Flask (WSGI) con plantillas Jinja2 y HTMX servido localmente; en Docker se sirve con Gunicorn.
- SQLite para los apuntes y un directorio en disco para las imágenes.
- Todo local: ningún dato sale del servidor (sin LLM, sin APIs externas, sin CDN).
- Pensada para un solo usuario en un servidor de la red doméstica (LAN).

Rutas principales:

- `GET /`: portada con el resumen de gastos y las últimas facturas; `GET /recent?page=N` devuelve las siguientes páginas («Ver siguientes»).
- `GET /upload`: formulario de subida; `POST /uploads` hace el OCR y muestra el borrador en revisión.
- `POST /drafts/<id>/confirm` y `POST /drafts/<id>/cancel`: guardar o descartar el borrador; `GET /drafts/<id>/image` sirve su foto.
- `GET /entries`: listado de apuntes; `/entries/<id>` (detalle), `/entries/<id>/edit`, `/entries/<id>/delete` y `/entries/<id>/image`.
- `GET /health`: estado en JSON.

## ⚠️ Sin autenticación

**La aplicación no tiene autenticación ni control de acceso.** Cualquier dispositivo de la LAN que llegue al puerto publicado tiene acceso completo: puede ver, subir, modificar y borrar apuntes y descargar todas las fotos de facturas, que contienen datos personales y fiscales (NIF, importes, direcciones).

- **No redirijas el puerto desde el router ni lo expongas a Internet** (tampoco mediante UPnP, túneles o proxies públicos). Úsala solo en una LAN de confianza.
- Limita la interfaz en la que Docker publica el puerto con `BIND_ADDRESS` (por defecto `0.0.0.0`, todas las interfaces). Por ejemplo, `BIND_ADDRESS=192.168.1.10` la publica solo en esa IP del servidor y `BIND_ADDRESS=127.0.0.1` solo en el propio servidor.
- El tráfico va por HTTP sin TLS y no hay protección CSRF.
- Un **inicio de sesión simple es una mejora futura prevista**, junto con la validación de `Origin`/`Host`.

## Requisitos previos

**Desarrollo local**

- Python 3.12.
- Tesseract con el idioma `spa` (opcional). Solo hace falta para hacer OCR real en local; las pruebas usan `FakeOcrEngine` y no lo necesitan. Si Tesseract no está instalado, la aplicación arranca igualmente, `/health` informa del OCR como `degraded` y cada subida muestra un borrador vacío con un aviso de fallo de OCR para rellenarlo a mano. En Windows, instala Tesseract con los datos de idioma español y añade `tesseract.exe` al `PATH`.

**Docker**

- Docker Engine con Docker Compose v2 (`docker compose`). La imagen ya incluye Tesseract y `tesseract-ocr-spa`.

## Arranque en local (Windows, PowerShell)

```powershell
# 1. Entorno virtual con Python 3.12
py -3.12 -m venv .venv
# alternativa con uv:
# uv venv --python 3.12 --seed .venv

# 2. Dependencias (ejecución + pruebas)
.venv\Scripts\pip install -r requirements-dev.txt

# 3. Rutas locales de datos (los valores por defecto son rutas Linux del contenedor: /data/...)
$env:INVOICE_DB_PATH = ".\data\db\invoices.db"
$env:INVOICE_IMAGES_DIR = ".\data\images"

# 4. Servidor de desarrollo de Flask en http://127.0.0.1:8000
.venv\Scripts\python -m app.main
```

Abre http://127.0.0.1:8000/ en el navegador. Los directorios de datos se crean al arrancar. Las variables `$env:` solo duran en la sesión actual de PowerShell.

- El servidor de desarrollo escucha solo en `127.0.0.1` (no se expone en la LAN). El puerto se cambia con `$env:INVOICE_PORT`.
- Comprobar el estado: `Invoke-RestMethod http://127.0.0.1:8000/health`.
- **Gunicorn no funciona en Windows** (depende de `fork`). Se instala con `requirements.txt` pero solo se ejecuta dentro del contenedor.
- Si una variable numérica (`INVOICE_PORT`, `INVOICE_MAX_UPLOAD_BYTES`) no es un entero positivo, o un directorio de datos no admite escritura, la aplicación registra el error con la ruta afectada y termina con código 1.

Variante Linux/macOS (bash):

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export INVOICE_DB_PATH=./data/db/invoices.db INVOICE_IMAGES_DIR=./data/images
.venv/bin/python -m app.main
```

## Pruebas y cobertura

```powershell
# Pruebas unitarias y de propiedades (excluye el marcador "tesseract"; no requiere Tesseract ni red)
.venv\Scripts\python -m pytest

# Cobertura mínima del 80 % por fichero de app/ (usa el coverage.json generado por pytest)
.venv\Scripts\python tools\check_coverage.py coverage.json --min 80
```

`pytest` genera `coverage.json` (opciones en `pyproject.toml`). `check_coverage.py` termina con código 0 si todos los ficheros de `app/` llegan al mínimo, 1 si alguno no llega (y los lista) y 2 si el informe no existe o no es válido.

**Prueba de integración con Tesseract real** (`pytest -m tesseract`, manual). Se ejecuta dentro del contenedor, que es donde está Tesseract. La imagen no incluye `tests/`, `pyproject.toml` ni las dependencias de desarrollo (quedan fuera por `.dockerignore` y solo se instala `requirements.txt`), así que se montan desde el repositorio en un contenedor efímero. Desde la raíz del proyecto, en el servidor (bash):

```bash
docker compose run --rm --user root \
  -v "$PWD/tests:/app/tests:ro" \
  -v "$PWD/pyproject.toml:/app/pyproject.toml:ro" \
  -v "$PWD/requirements-dev.txt:/app/requirements-dev.txt:ro" \
  invoice-reader \
  sh -c "pip install --no-cache-dir -r requirements-dev.txt && python -m pytest -m tesseract"
```

Se usa `--user root` solo porque `appuser` no puede instalar paquetes en la imagen; `--rm` elimina el contenedor al terminar.

## Compilar y ejecutar con Docker

### Con Docker Compose (recomendado)

```bash
docker compose build          # construye la imagen
docker compose up -d          # arranca en segundo plano
# o ambos pasos a la vez (sin -d, en primer plano con los logs: docker compose up --build)
docker compose up -d --build
```

La aplicación queda en `http://<ip-del-servidor>:8000/`. Dentro del contenedor, Gunicorn (`app.wsgi:app`, configurado en `gunicorn.conf.py`: 2 *workers* síncronos, `timeout` 90 s) escucha en `0.0.0.0:INVOICE_PORT` con el usuario sin privilegios `appuser` (uid 10001). El esquema de la base de datos se crea automáticamente en el primer arranque. El servicio de Compose se llama `invoice-reader`, usa `restart: unless-stopped` y el `HEALTHCHECK` de la imagen consulta `/health` con `urllib` de Python cada 30 s.

Puerto e interfaz publicados, por variables de entorno del shell o en un fichero `.env` junto a `docker-compose.yml`:

```bash
BIND_ADDRESS=192.168.1.10 INVOICE_HOST_PORT=8080 docker compose up -d
```

Estado, logs y mantenimiento:

```bash
docker compose ps                        # la columna STATUS muestra (healthy) cuando /health responde 200
curl http://<ip-del-servidor>:8000/health
docker compose logs -f invoice-reader    # Gunicorn indica "Listening at: http://0.0.0.0:8000"
docker compose down                      # para y elimina el contenedor; los volúmenes (datos) se conservan
git pull && docker compose up -d --build # actualizar a una nueva versión
```

`docker compose down -v` **borra los volúmenes y con ellos todos los apuntes y fotos**.

### Con `docker build` y `docker run`

Equivalente sin Compose:

```bash
docker build -t invoice-reader .

docker run -d --name invoice-reader --restart unless-stopped \
  -p 0.0.0.0:8000:8000 \
  -e INVOICE_PORT=8000 \
  -e INVOICE_MAX_UPLOAD_BYTES=10485760 \
  -v invoice-db:/data/db \
  -v invoice-images:/data/images \
  invoice-reader

docker inspect --format '{{.State.Health.Status}}' invoice-reader
docker logs -f invoice-reader
```

Cambia `0.0.0.0` por la IP de la interfaz que quieras usar (ver la sección «⚠️ Sin autenticación»). Compose antepone el nombre del proyecto a sus volúmenes (por ejemplo `factur_invoice-db`), así que los volúmenes de `docker run` son distintos de los de Compose.

### Persistencia

- Los datos viven en dos volúmenes con nombre: `invoice-db` (montado en `/data/db`, contiene `invoices.db`) e `invoice-images` (montado en `/data/images`, con los borradores temporales en `/data/images/tmp`). Sobreviven a `docker compose down`, a recrear el contenedor y a actualizar la imagen.
- Para usar *bind mounts* (directorios del servidor) en lugar de volúmenes, sustituye en `docker-compose.yml` `invoice-db:/data/db` e `invoice-images:/data/images` por las rutas del host y dales como propietario el uid del contenedor antes de arrancar:

  ```bash
  sudo mkdir -p /srv/invoice-reader/db /srv/invoice-reader/images
  sudo chown -R 10001:10001 /srv/invoice-reader
  ```

  Sin ese `chown` el arranque falla: la aplicación registra la ruta no escribible y el contenedor termina con código distinto de cero.
- En desarrollo local los datos quedan en `data/` (base de datos y fotos con datos personales). Ese directorio está excluido de Git en `.gitignore`; no lo añadas al repositorio.

## Configuración

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `INVOICE_DB_PATH` | `/data/db/invoices.db` | Ruta del fichero SQLite. Su directorio se crea al arrancar. |
| `INVOICE_IMAGES_DIR` | `/data/images` | Directorio de las fotos de facturas (los borradores se guardan en `tmp/` y se purgan a las 24 h). |
| `INVOICE_MAX_UPLOAD_BYTES` | `10485760` (10 MB) | Tamaño máximo de subida en bytes. |
| `INVOICE_PORT` | `8000` | Puerto interno de escucha: Gunicorn en `0.0.0.0` dentro del contenedor; servidor de desarrollo en `127.0.0.1` en local. |
| `BIND_ADDRESS` | `0.0.0.0` | Solo `docker-compose.yml`. IP del servidor en la que se publica el puerto. |
| `INVOICE_HOST_PORT` | `8000` | Solo `docker-compose.yml`. Puerto publicado en el servidor. |

- Las variables numéricas deben ser enteros positivos; si no, la aplicación (o Gunicorn, en el contenedor) termina con un código distinto de cero.
- En Docker, `INVOICE_DB_PATH` e `INVOICE_IMAGES_DIR` se dejan con su valor por defecto para que coincidan con los volúmenes montados en `/data/db` y `/data/images`.

## Estructura del proyecto

```
app/
  main.py          create_app(), load_app(), run() (servidor de desarrollo)
  wsgi.py          app = load_app()  → gunicorn app.wsgi:app
  config.py        Settings desde variables de entorno
  uploads.py, ocr.py, extractor.py, tax_id.py, validation.py,
  storage.py, repository.py, services.py, formatting.py,
  health.py, security.py, models.py
  routes/          Blueprints: pages, entries, images, health
  templates/       plantillas Jinja2
  static/          app.css, htmx.min.js (vendorizado)
tests/
  conftest.py, fakes.py (FakeOcrEngine), strategies.py (generadores Hypothesis)
  unit/            pruebas unitarias y de propiedades (Hypothesis)
  integration/     test_tesseract.py (@pytest.mark.tesseract, manual)
tools/check_coverage.py   cobertura mínima por fichero
gunicorn.conf.py          configuración de Gunicorn (bind, workers, timeout)
Dockerfile, docker-compose.yml, .dockerignore
requirements.txt, requirements-dev.txt   dependencias con versiones fijadas (==)
pyproject.toml            configuración de pytest y coverage
```
