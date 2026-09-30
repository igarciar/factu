# Plan de implementación: Invoice Reader

## Visión general

Implementación incremental en Python 3.12 del monolito Flask (WSGI) descrito en `design.md`, servido con Gunicorn dentro del contenedor. Primero se construye el núcleo puro (subidas, NIF/CIF, extractor, validación), después la E/S (almacén de imágenes, repositorio SQLite, OCR), los servicios, la capa web y, por último, el empaquetado Docker y la documentación. Cada propiedad de corrección se implementa como prueba Hypothesis (`@settings(max_examples=100)` como mínimo) junto al componente que valida, en `tests/unit/test_*_properties.py`, con el comentario `# Feature: invoice-reader, Property N: <título>`.

El host de desarrollo es Windows: los comandos se ejecutan con el Python del entorno virtual (`py -3.12 -m venv .venv` y `.venv\Scripts\python -m pytest`). La ejecución por defecto de `pytest` excluye `-m tesseract`, por lo que no requiere Tesseract instalado; el OCR se sustituye por `FakeOcrEngine`. Gunicorn no funciona en Windows: en local se usan el cliente de pruebas de Flask (`app.test_client()`) y, si hace falta, el servidor de desarrollo de Flask (`.venv\Scripts\python -m app.main`); Gunicorn solo se ejecuta dentro del contenedor.

## Tareas

- [x] 1. Estructura del proyecto, configuración y utilidades de prueba
  - [x] 1.1 Crear el esqueleto del proyecto y las dependencias fijadas
    - Crear (o conservar si ya existen) `app/__init__.py`, `app/routes/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`
    - Reescribir `requirements.txt` con versiones `==` exactas según el diseño: `flask==3.1.3`, `werkzeug==3.1.9`, `jinja2==3.1.6`, `markupsafe==3.0.3`, `itsdangerous==2.2.0`, `click==8.5.0`, `blinker==1.9.0`, `gunicorn==26.2.0`, `pytesseract==0.3.13`, `pillow==12.3.0`
    - Reescribir `requirements-dev.txt`: `-r requirements.txt`, `pytest==9.1.1`, `pytest-cov==7.1.0`, `hypothesis==6.168.3`
    - Eliminar `fastapi`, `uvicorn`, `python-multipart` y `httpx` si aparecen en los ficheros existentes; reinstalar el entorno con `.venv\Scripts\pip install -r requirements-dev.txt`
    - Crear `pyproject.toml` con `[tool.pytest.ini_options]` (`addopts = "-m 'not tesseract' --cov=app --cov-branch --cov-report=term-missing --cov-report=json"`, marcador `tesseract`) y `[tool.coverage.run]` (`branch = true`, `source = ["app"]`)
    - _Requirements: 15.1, 15.2, 15.3_

  - [x] 1.2 Implementar `app/config.py`
    - `Settings` (dataclass congelada) con los valores por defecto del diseño y `from_env(env)` leyendo `INVOICE_DB_PATH`, `INVOICE_IMAGES_DIR`, `INVOICE_MAX_UPLOAD_BYTES`, `INVOICE_PORT`
    - `ConfigError` si algún entero no es positivo o no se puede leer; `host` fijo `0.0.0.0`, `ocr_timeout_seconds=60`, `draft_ttl_hours=24`
    - _Requirements: 1.3, 11.1, 11.2, 11.3, 12.1, 12.2_

  - [x]* 1.3 Escribir pruebas unitarias de `Settings`
    - Valores por defecto, lectura de variables de entorno, `ConfigError` con valores negativos, cero o no numéricos
    - _Requirements: 1.3, 11.1, 11.2, 11.3, 12.2_

  - [x] 1.4 Implementar `app/models.py`
    - `EntryType`, `Draft`, `EntryInput`, `Entry`, `EntryFilter`, `Page[T]`, `Totals`, `DraftFiles`, `DraftView`, `ConfirmResult` como dataclasses congeladas según el diseño
    - _Requirements: 3.1, 6.1, 8.2, 8.3_

  - [x] 1.5 Crear `tests/conftest.py` y `tests/strategies.py`
    - Fixture `settings` que apunta `db_path` e `images_dir` a `tmp_path`
    - Estrategias Hypothesis compartidas: fechas 1900–2099, `Decimal` con dos decimales en `[0, 10^9)`, NIF/NIE/CIF válidos con control calculado de forma independiente (oráculo), variantes con espacios/guiones/minúsculas, líneas de ruido, bytes JPEG/PNG mínimos con firma
    - _Requirements: 15.2, 15.4_

  - [x] 1.6 Implementar `tools/check_coverage.py`
    - CLI `python tools/check_coverage.py coverage.json --min 80` que recorre `files[*].summary.percent_covered`, lista los ficheros de `app/` por debajo del mínimo y termina con código 1 si hay alguno (0 en otro caso)
    - Rutas tolerantes a separadores `\` y `/` (host Windows y contenedor Linux)
    - _Requirements: 15.3_

  - [x]* 1.7 Escribir pruebas unitarias de `tools/check_coverage.py`
    - JSON con todos los ficheros ≥ 80 % → código 0; un fichero por debajo → código 1 y nombre en la salida; ficheros fuera de `app/` ignorados; rutas con `\`
    - _Requirements: 15.3_

  - [x] 1.8 Añadir a `app/models.py` los modelos de la Portada
    - `YearSummary`, `MonthlyComparison`, `ExpenseSummary` (exactamente 12 meses) y `RecentPage` (`items`, `page`, `page_size`, `has_more`) como dataclasses congeladas según el diseño
    - _Requirements: 16.3, 16.4, 16.6, 16.8, 16.10_

  - [x] 1.9 Implementar `app/formatting.py`
    - `MONTH_NAMES_ES`, `MAX_RECENT_PAGE = 100_000`, `format_eur(value)` (sin `locale`, `ROUND_HALF_UP`, miles con `.`, coma decimal, sufijo `" €"`, `None` → `""`) y `parse_recent_page(raw)` (solo dígitos ASCII en `1..100000`; en otro caso 1)
    - _Requirements: 16.6, 16.15, 16.18_

  - [x]* 1.10 Escribir prueba de propiedad del formato español de importes
    - **Property 24: Round-trip del formato español de importes**
    - Usa `parse_amount` de `app/extractor.py` (tarea 4.1) como inversa
    - **Validates: Requirements 16.18**

  - [x]* 1.11 Escribir prueba de propiedad de `parse_recent_page`
    - **Property 25: Normalización del número de Página_Recientes**
    - **Validates: Requirements 16.15**

  - [x]* 1.12 Escribir pruebas unitarias de `app/formatting.py`
    - `format_eur`: `None`, `0` → `"0,00 €"`, `Decimal("999.99")`, `Decimal("1000")` → `"1.000,00 €"`, `Decimal("1234567.805")` → `"1.234.567,81 €"`; `parse_recent_page`: `None`, `""`, `"abc"`, `"0"`, `"-1"`, `"1e3"`, `"٣"` (dígito no ASCII), `"100001"` → 1; `"2"` → 2; `MONTH_NAMES_ES` con 12 nombres de Enero a Diciembre
    - _Requirements: 16.6, 16.15, 16.18_

- [x] 2. Validación de subidas (`app/uploads.py`)
  - [x] 2.1 Implementar `detect_format` y `validate_upload`
    - Excepciones `UploadError`, `MissingFile`, `UnsupportedFormat`, `FileTooLarge`; firmas `JPEG_MAGIC` y `PNG_MAGIC`
    - Orden de comprobación: vacío → tamaño → firma; ignorar `filename` y `content_type`
    - _Requirements: 1.2, 1.4, 1.5, 1.6, 14.5_

  - [x]* 2.2 Escribir prueba de propiedad de clasificación de subidas
    - **Property 1: Clasificación de subidas por firma y tamaño**
    - **Validates: Requirements 1.2, 1.4, 1.5, 14.5**

  - [x]* 2.3 Escribir pruebas unitarias de `uploads.py`
    - `None`, bytes vacíos, tamaño exactamente igual a `max_bytes` y `max_bytes + 1`, firma PNG truncada
    - _Requirements: 1.4, 1.5, 1.6_

- [x] 3. Identificadores fiscales (`app/tax_id.py`)
  - [x] 3.1 Implementar `normalize`, `kind` e `is_valid`
    - Letra de control de NIF (`n % 23`), NIE con sustitución X/Y/Z, CIF con dígito/letra de control según la letra inicial
    - _Requirements: 3.3, 5.6_

  - [x]* 3.2 Escribir pruebas unitarias de `tax_id.py`
    - Ejemplos conocidos válidos e inválidos de NIF, NIE y CIF (control dígito, letra y ambos); formatos no reconocidos → `kind is None`
    - _Requirements: 3.3, 5.6_

- [x] 4. Extractor de campos (`app/extractor.py`)
  - [x] 4.1 Implementar los parsers básicos
    - Regex compiladas como constantes del módulo; `parse_date` (separador idéntico, años de dos cifras → `2000 + aa`, descarte de fechas inválidas), `parse_amount` (reglas de coma/punto y `quantize` `ROUND_HALF_UP`), `find_dates`
    - _Requirements: 3.2, 3.4, 3.10_

  - [x] 4.2 Implementar los buscadores y `extract`
    - `find_tax_ids` (regex tolerante a separadores + `tax_id.normalize` + patrones), `find_labeled_amount` (normalización de tildes con `unicodedata`, etiquetas de más a menos específicas, exclusión de "subtotal" y de porcentajes), `find_invoice_number`, `find_supplier`
    - `extract(text) -> Draft`: fecha tras la etiqueta "Fecha" o primera válida; importes serializados como `"1234.56"`; concepto y categoría vacíos; `entry_type="gasto"` nunca en `missing`; sin estado global, reloj ni aleatoriedad
    - _Requirements: 3.1, 3.3, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [x]* 4.3 Escribir prueba de propiedad del round-trip de fechas
    - **Property 2: Round-trip de fechas**
    - **Validates: Requirements 3.2**

  - [x]* 4.4 Escribir prueba de propiedad de normalización de NIF/NIE/CIF
    - **Property 3: Normalización de NIF/NIE/CIF**
    - **Validates: Requirements 3.3**

  - [x]* 4.5 Escribir prueba de propiedad del round-trip de importes
    - **Property 4: Round-trip de importes**
    - **Validates: Requirements 3.4**

  - [x]* 4.6 Escribir prueba de propiedad de asignación de importes por etiqueta
    - **Property 5: Asignación de importes por etiqueta**
    - **Validates: Requirements 3.5**

  - [x]* 4.7 Escribir prueba de propiedad de extracción del número de factura
    - **Property 6: Extracción del número de factura**
    - **Validates: Requirements 3.6**

  - [x]* 4.8 Escribir prueba de propiedad del proveedor
    - **Property 7: Proveedor como primera línea significativa**
    - **Validates: Requirements 3.7**

  - [x]* 4.9 Escribir prueba de propiedad del invariante del Borrador y determinismo
    - **Property 8: Invariante del Borrador y determinismo**
    - **Validates: Requirements 3.1, 3.8, 3.9, 3.10**

  - [x]* 4.10 Escribir pruebas unitarias del extractor
    - Factura de ejemplo completa en español, fechas imposibles (31/02), separadores mezclados, "Subtotal" frente a "Total", línea de IVA con `21%`, texto vacío
    - _Requirements: 3.1–3.9_

  - [ ] 4.11 Ampliar el extractor para facturas con importes en tabla y encabezado FACTURA
    - En `app/extractor.py`, ampliar `BASE_LABELS`, `VAT_LABELS` y `TOTAL_LABELS` con los sinónimos plegados del diseño (base: "total si", "base imponible (eur)"; IVA: "total iva", "total iva/igic/ipsi", "cuota"; total: "total tii", "total (eur)", "importe total (eur)"), colocando cada variante específica antes de su prefijo
    - En `find_labeled_amount`/`_amount_after_label`, cuando la línea de la etiqueta no lleva Importe, buscar el Importe en la siguiente línea no vacía (mapeo posicional fila-etiquetas → fila-números por orden de columna); mantener la exclusión del sufijo `%` y no elegir nunca el valor de la columna "Tasa" (preferir la etiqueta "Total IVA..." más específica)
    - En `INVOICE_NUMBER_RE`, añadir la rama de encabezado desnudo `FACTURA <n>` (sin "nº"), token `[A-Z0-9][A-Z0-9/\-.]{0,29}`; conservar las etiquetas del 3.6
    - En `DATE_LABEL_RE`, añadir la etiqueta "Fecha de venta" como ancla adicional, conservando "Fecha" y la primera fecha válida como respaldo
    - Mantener la pureza y el determinismo (sin estado global, reloj ni aleatoriedad; regex y listas como constantes del módulo)
    - _Requirements: 3.11, 3.12, 3.13, 3.14, 3.15, 3.10_

  - [ ]* 4.12 Ampliar la prueba de propiedad de asignación de importes por etiqueta
    - **Property 5: Asignación de importes por etiqueta** (ampliar generadores con los nuevos sinónimos de etiqueta)
    - **Validates: Requirements 3.5, 3.14**

  - [ ]* 4.13 Escribir prueba de propiedad de importe con etiqueta en la línea anterior y exclusión de "Tasa"
    - **Property 26: El IVA no toma el valor de la columna "Tasa"/porcentaje**
    - **Property 27: Importe con etiqueta en la línea anterior**
    - **Validates: Requirements 3.13, 3.14**

  - [ ]* 4.14 Ampliar la prueba de propiedad del número de factura y de la etiqueta "Fecha de venta"
    - **Property 6: Extracción del número de factura** (ampliar con el encabezado desnudo `FACTURA <n>`)
    - **Property 28: Número de factura tras el encabezado "FACTURA" y ancla "Fecha de venta"**
    - **Validates: Requirements 3.6, 3.11, 3.12**

  - [ ]* 4.15 Escribir prueba unitaria con el Texto_OCR real de la factura de Leroy Merlin como fixture
    - Fixture con el Texto_OCR real de la factura de Leroy Merlin (formato tabla) — **el texto OCR se captura por separado**; guardarlo como fixture y marcar claramente su origen
    - Comprobar que `extract` propone número de factura (`FACTURA 050-0008-327711` → `050-0008-327711`), fecha ("Fecha de venta"), base, IVA (columna "Total IVA...", no la columna "Tasa") y total; documentar como comentario los campos que queden sin detectar por el aplanado del OCR (mejor esfuerzo, 3.15)
    - _Requirements: 3.11, 3.12, 3.13, 3.14, 3.15_

- [x] 5. Validación del formulario (`app/validation.py`)
  - [x] 5.1 Implementar `ValidationResult` y `validate_entry`
    - Obligatorios `invoice_date`, `entry_type`, `total`; fecha ISO válida; tipo en `{gasto, ingreso}`; importes con `parse_amount` y `≥ 0`
    - Descuadre `|base + iva − total| > 0.01` → `warnings["totals"]` y bloqueo salvo `mismatch_confirmed`; NIF inválido → `warnings["tax_id"]` no bloqueante
    - `strip()` y longitudes máximas (proveedor/concepto 200, categoría 60, número 40)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 9.1_

  - [x]* 5.2 Escribir prueba de propiedad de validación de obligatorios, fecha, tipo e importes
    - **Property 11: Validación de campos obligatorios, fecha, tipo e importes**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 9.1**

  - [x]* 5.3 Escribir prueba de propiedad del aviso de descuadre
    - **Property 12: Aviso de descuadre**
    - **Validates: Requirements 5.5**

  - [x]* 5.4 Escribir prueba de propiedad de validación de NIF/CIF no bloqueante
    - **Property 13: Validación de NIF/CIF no bloqueante**
    - **Validates: Requirements 5.6**

  - [x]* 5.5 Escribir pruebas unitarias de `validation.py`
    - Descuadre exactamente de 0,01, importes con formato español, longitudes límite, campos solo con espacios
    - _Requirements: 5.1–5.6_

- [x] 6. Checkpoint: núcleo puro
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Almacén de imágenes (`app/storage.py`)
  - [x] 7.1 Implementar `ImageStore`
    - `UUID_NAME`, `DRAFT_ID`; `save_temp` (`.part` + `fsync` + `os.replace`, sin transformar bytes), `save_temp_text`, `load_draft`, `promote` (nuevo `uuid4().hex`), `demote`, `discard_draft`, `delete`, `resolve` (regex + `.resolve()` + `is_relative_to` + existencia), `purge_expired_drafts(now, ttl)` por `mtime`
    - `purge_expired_drafts` idempotente y segura con varios *workers* de Gunicorn: ignora `FileNotFoundError` en `stat()` y `unlink()` y devuelve solo lo que ha borrado
    - _Requirements: 4.5, 4.6, 6.2, 6.3, 14.3, 14.4, 14.5_

  - [x]* 7.2 Escribir prueba de propiedad de purga de Borradores caducados
    - **Property 10: Purga de Borradores caducados**
    - **Validates: Requirements 4.6**

  - [x]* 7.3 Escribir pruebas unitarias de `ImageStore`
    - `resolve` con `..`, rutas absolutas, `\`, byte nulo y nombres válidos inexistentes → `None`; `promote`/`demote` ida y vuelta; `delete` de fichero ausente → `False`; bytes guardados idénticos
    - _Requirements: 6.3, 9.4, 14.3, 14.4_

- [x] 8. Repositorio SQLite (`app/repository.py`)
  - [x] 8.1 Implementar `init_schema`, la conexión y `CategoryRepository`
    - Tablas, índice y restricciones del diseño; `PRAGMA foreign_keys=ON` y `journal_mode=WAL`; conexión con `timeout=5`; `init_schema` dentro de `BEGIN IMMEDIATE` (idempotente con varios procesos); semilla de categorías con `INSERT OR IGNORE`; `list` y `ensure` con marcadores `?`
    - _Requirements: 7.1, 7.3, 10.5, 14.6_

  - [x] 8.2 Implementar `EntryRepository`
    - `insert`, `update`, `get`, `delete`, `list(flt, page, page_size=20)` ordenado por `(invoice_date DESC, id DESC)`, `totals(flt)`; importes en céntimos enteros ↔ `Decimal`; `WHERE` construido con fragmentos fijos y lista de parámetros
    - _Requirements: 6.1, 6.4, 8.1, 8.2, 8.3, 8.4, 9.1, 9.3, 14.6_

  - [x]* 8.3 Escribir prueba de propiedad del listado ordenado y paginado
    - **Property 18: Listado ordenado y paginado**
    - **Validates: Requirements 8.1, 8.2**

  - [x]* 8.4 Escribir prueba de propiedad de filtros y totales frente a un modelo de referencia
    - **Property 19: Filtros y totales frente a un modelo de referencia**
    - **Validates: Requirements 8.3, 8.4**

  - [x]* 8.5 Escribir pruebas unitarias del repositorio
    - Categorías semilla, `init_schema` idempotente, `UNIQUE` de `image_filename`, `get`/`update`/`delete` de ids inexistentes, página fuera de rango
    - _Requirements: 6.4, 7.1, 10.5, 11.5_

  - [x] 8.6 Añadir a `EntryRepository` las consultas e índices de la Portada
    - En `init_schema`: `ix_entries_created (created_at DESC, id DESC)` e `ix_entries_type_date (entry_type, invoice_date)` con `CREATE INDEX IF NOT EXISTS`
    - `insert` guarda `created_at` en UTC con formato fijo `isoformat(timespec="microseconds")` para que el orden de texto sea cronológico
    - `monthly_expense_totals(conn, first_year, last_year) -> dict[(año, mes), céntimos]`: `SUM(total_cents)` con `GROUP BY` de `strftime('%Y')`/`strftime('%m')`, filtro `entry_type = ?` y rango `invoice_date >= ? AND < ?`, todo parametrizado
    - `recent(conn, offset, limit=10) -> (items, has_more)`: `ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?` pidiendo `limit + 1` filas
    - _Requirements: 16.3, 16.4, 16.5, 16.6, 16.8, 16.10, 16.14, 16.16, 14.6_

  - [x]* 8.7 Escribir prueba de propiedad de la paginación de Últimas_Facturas
    - **Property 23: Paginación de Últimas_Facturas sin duplicados ni omisiones**
    - Generar `created_at` con empates frecuentes y ambos Tipos; fichero `tests/unit/test_recent_properties.py`
    - **Validates: Requirements 16.8, 16.10, 16.14, 16.16**

  - [x]* 8.8 Escribir pruebas unitarias de las consultas de la Portada
    - En `tests/unit/test_repository_dashboard.py`: BD vacía → `{}` y `([], False)`; gastos del 31/12 y del 1/1 en el año correcto; ingresos excluidos; años fuera del rango excluidos; empate de `created_at` resuelto por `id`; exactamente 10 y 11 Apuntes (`has_more`); índices presentes con `PRAGMA index_list(entries)`
    - _Requirements: 16.3, 16.4, 16.5, 16.7, 16.8, 16.14, 16.17_

- [x] 9. Motor OCR (`app/ocr.py`)
  - [x] 9.1 Implementar `OcrEngine`, `TesseractOcrEngine` y `FakeOcrEngine`
    - `OcrError`, `OcrTimeout`, `OcrUnavailable`, `OcrStatus`; Pillow + `ImageOps.exif_transpose` solo para OCR; `pytesseract.image_to_string(img, lang="spa", timeout=60)`; traducción de excepciones; `status()` con `get_languages()`
    - Crear `tests/fakes.py` con `FakeOcrEngine(text=None, raises=None, status=...)`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 15.2_

  - [x]* 9.2 Escribir pruebas unitarias de `TesseractOcrEngine` con `pytesseract` simulado
    - `monkeypatch` de `pytesseract`: argumentos `lang="spa"` y `timeout=60`, `RuntimeError` de timeout → `OcrTimeout`, `TesseractNotFoundError`/idioma ausente → `OcrUnavailable`, `status()` con y sin `spa`
    - _Requirements: 2.1, 2.3, 2.4, 15.2_

  - [x]* 9.3 Escribir prueba de integración con Tesseract real
    - `tests/integration/test_tesseract.py` con `@pytest.mark.tesseract`; factura sintética en español generada con Pillow; excluida de la ejecución por defecto (`pytest -m tesseract` dentro del contenedor)
    - _Requirements: 2.1, 2.2_

- [x] 10. Servicios (`app/services.py`)
  - [x] 10.1 Implementar `DraftService`
    - `create(data, fmt)`: `uuid4().hex`, `save_temp`, OCR como llamada bloqueante normal (sin *threadpool*; se ejecuta en el *worker* síncrono), `OcrTimeout`/`OcrUnavailable` → `logger.error` y Borrador vacío `notice="ocr_failed"`, texto en blanco → `notice="no_text"`, en otro caso `extract`; `save_temp_text`; `cancel(draft_id)` con `discard_draft`
    - `DraftPurger(store, ttl, interval=1h, clock, monotonic)`: `purge_now()` y `maybe_purge()` (solo purga si ha pasado `interval` desde la última purga del proceso; los fallos inesperados se registran con `logger.exception`)
    - _Requirements: 2.3, 2.4, 2.5, 4.1, 4.4, 4.5, 4.6_

  - [x] 10.2 Implementar `EntryService`
    - `confirm`: `load_draft` (404 si no existe), `validate_entry`, transacción `BEGIN IMMEDIATE` con `categories.ensure`, `promote`, `insert`, `commit`; ante fallo `rollback` + `demote` + `logger.exception` + `SaveError`; el Borrador sigue disponible
    - `update` con la misma validación; `delete` con borrado de fila, `store.delete` tras el `commit` y `logger.warning` si la imagen no existía
    - _Requirements: 5.7, 6.1, 6.2, 6.3, 6.4, 6.5, 7.3, 9.1, 9.3, 9.4_

  - [x]* 10.3 Escribir prueba de propiedad del round-trip de guardado
    - **Property 14: Round-trip de guardado de Apunte e imagen**
    - **Validates: Requirements 6.1, 6.3, 14.6**

  - [x]* 10.4 Escribir prueba de propiedad del nombre de imagen generado y único
    - **Property 15: Nombre de imagen generado y único**
    - **Validates: Requirements 6.2, 6.4, 14.5**

  - [x]* 10.5 Escribir prueba de propiedad de atomicidad ante fallos
    - **Property 16: Atomicidad ante fallos**
    - Inyección de fallos con `monkeypatch` en `ImageStore.promote`, `EntryRepository.insert` y un envoltorio de conexión cuyo `commit` lanza excepción
    - **Validates: Requirements 6.5**

  - [x]* 10.6 Escribir prueba de propiedad de incorporación de categorías
    - **Property 17: Las categorías nuevas se incorporan a las sugerencias**
    - **Validates: Requirements 7.3**

  - [x]* 10.7 Escribir prueba de propiedad de eliminación de Apunte e imagen
    - **Property 21: La eliminación borra Apunte e imagen**
    - **Validates: Requirements 9.3**

  - [x]* 10.8 Escribir pruebas unitarias de los servicios
    - Avisos `ocr_failed`/`no_text` con `FakeOcrEngine`; tras subir no hay Apuntes; cancelar elimina temporales; borrado con imagen ausente → `warning` y éxito; `update` de id inexistente
    - `DraftPurger.maybe_purge`: no purga antes de `interval`, purga después; `FileNotFoundError` durante la purga no produce error
    - _Requirements: 2.3, 2.4, 2.5, 4.4, 4.5, 4.6, 9.4_

  - [x] 10.9 Implementar `DashboardService` en `app/services.py`
    - `DashboardService(entries, connect, clock)`; `expense_summary()`: Año_Actual = `clock().year`, año anterior = Año_Actual − 1, una llamada a `monthly_expense_totals`, 12 `MonthlyComparison` con 0 en meses sin gastos, totales anuales como suma entera de los 12 meses convertida a `Decimal`
    - `recent(page)`: `offset = (page − 1) × 10`, `RecentPage(items, page, 10, has_more)`
    - _Requirements: 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.17_

  - [x]* 10.10 Escribir prueba de propiedad del resumen de gastos
    - **Property 22: Resumen de gastos frente a un modelo de referencia**
    - Reloj generado por Hypothesis (instantes arbitrarios, incluidos 1 de enero y 31 de diciembre); fichero `tests/unit/test_dashboard_properties.py`
    - **Validates: Requirements 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.17**

  - [x]* 10.11 Escribir pruebas unitarias de `DashboardService`
    - En `tests/unit/test_dashboard_service.py`: BD vacía → todo `0.00` y 12 meses; reloj fijo en un año concreto; `recent(1)` y `recent(2)` con 11 Apuntes; `recent` de una página sin datos → `items == []`, `has_more is False`
    - _Requirements: 16.2, 16.7, 16.8, 16.14, 16.17_

- [x] 11. Checkpoint: núcleo y persistencia
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Capa web
  - [x] 12.1 Implementar `app/security.py` y `app/health.py`
    - `security.register(app)`: `@app.after_request` que añade `X-Content-Type-Options: nosniff` y `Content-Security-Policy: default-src 'self'; img-src 'self'` a todas las respuestas
    - Comprobaciones de salud: `SELECT 1` con `timeout=1`, escritura y borrado de `tmp/.health-{uuid}`, estado OCR cacheado; cálculo de `status` (`ok`/`degraded`/`error`) y código 200/503
    - _Requirements: 13.1, 13.2, 13.3, 14.5_

  - [x] 12.2 Crear las plantillas base y los estáticos
    - `base.html` (viewport, `app.css`, `htmx.min.js` local, región `role="alert"`), `error.html`; `static/app.css` (una columna < 640 px, dos columnas en escritorio, foco visible, contraste AA); `static/htmx.min.js` vendorizado con versión fijada
    - Navegación común en `base.html` (`<nav aria-label="Principal">`): "Inicio" (`/`), "Subir factura" (`/upload`), "Apuntes" (`/entries`)
    - _Requirements: 1.1, 2.2, 12.4, 14.7, 16.1_

  - [x] 12.3 Implementar `app/routes/pages.py` con `upload.html` y `review.html`
    - *Blueprint* `pages`: `GET /upload` con el formulario de subida (`accept="image/jpeg,image/png"`, `capture="environment"`, `action="/uploads"`), `POST /uploads` (`request.files.get("file")` y `file.stream.read(max_bytes + 1)`, 400/413 con mensajes del diseño), `POST /drafts/<draft_id>/confirm` (303/422/500), `POST /drafts/<draft_id>/cancel` (303 → `/upload`); `GET /upload` y `POST /uploads` llaman a `DraftPurger.maybe_purge()`. La Portada `GET /` se implementa en la tarea 12.11
    - Servicios obtenidos con `current_app.extensions["invoice"]`; respuestas con `render_template(...)` y código explícito (`, 422`), `redirect(..., code=303)`
    - `review.html`: imagen, Texto_OCR en `<pre>` escapado, `field--missing` + "No detectado" con `aria-describedby`, avisos, casilla de confirmación de descuadre, `<datalist id="categories">`, `aria-invalid` en errores; `draft_id` validado con `DRAFT_ID`
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 4.1, 4.2, 4.3, 4.4, 4.5, 5.5, 5.7, 6.6, 7.2, 12.4_

  - [x] 12.4 Implementar `app/routes/entries.py` con sus plantillas
    - *Blueprint* `entries`: `GET /entries` con filtros y paginación desde `request.args` (parcial `_entries_table.html` si `request.headers.get("HX-Request")`), `GET /entries/<int:entry_id>` (mensaje `saved=1`), `GET/POST /entries/<int:entry_id>/edit`, `GET/POST /entries/<int:entry_id>/delete`
    - Plantillas `list.html` ("Página X de Y · 20 por página · N apuntes", sumas de gastos e ingresos con filtros), `_entries_table.html`, `detail.html`, `edit.html`, `confirm_delete.html`
    - _Requirements: 6.6, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.1, 9.2, 9.3_

  - [x] 12.5 Implementar `app/routes/images.py` y `app/routes/health.py`
    - *Blueprint* `images`: `GET /drafts/<draft_id>/image` y `GET /entries/<int:entry_id>/image` vía `ImageStore.resolve` y `send_file(path, mimetype=...)` con `Content-Type` según la extensión, `abort(404)` en otro caso
    - *Blueprint* `health`: `GET /health` devolviendo el JSON del diseño con `jsonify` y código 200/503
    - _Requirements: 4.2, 8.5, 13.1, 13.2, 13.3, 14.3, 14.4_

  - [x] 12.6 Implementar `app/main.py` y conectar todo
    - `create_app(settings, ocr_engine=None, clock=utc_now) -> Flask` (patrón *application factory*): `Flask(__name__, template_folder="templates", static_folder="static")` con autoescape de Jinja2 (por defecto en `.html`), `MAX_CONTENT_LENGTH = max_upload_bytes + 64 KiB`, `security.register(app)`, registro de los *Blueprints* de 12.3–12.5 y 12.11, servicios, `DraftPurger` y `DashboardService` (con el mismo `clock`) en `app.extensions["invoice"]`
    - Registrar el filtro Jinja `eur` con `app.add_template_filter(format_eur, "eur")`
    - Manejadores `@app.errorhandler`: 404 → `error.html`; 413 (`RequestEntityTooLarge`) → `upload.html` con el mensaje de tamaño máximo; 500 → `logger.exception` + `error.html` genérico
    - Arranque síncrono dentro de `create_app` (sin `lifespan`): `ensure_writable_dir` (crea y prueba escritura; `StartupError(path)`), `init_schema`, purga inicial con `DraftPurger.purge_now()`, caché de `ocr_engine.status()`
    - `load_app(env=os.environ)`: `Settings.from_env(env)` + `create_app`, *logging* básico; `ConfigError`/`StartupError` → `logger.error` con la ruta y `sys.exit(1)`
    - `run()`: solo desarrollo local (también en Windows), `load_app().run(host="127.0.0.1", port=settings.port)`; bloque `if __name__ == "__main__"` para `python -m app.main`
    - Crear `app/wsgi.py` con `app = load_app()` (callable WSGI para `gunicorn app.wsgi:app`)
    - Crear `gunicorn.conf.py` en la raíz: `bind = f"{settings.host}:{settings.port}"` a partir de `Settings.from_env()` (`0.0.0.0:INVOICE_PORT`), `workers = 2`, `worker_class = "sync"`, `timeout = 90`, `graceful_timeout = 30`, `preload_app = True`, `accesslog = "-"`, `errorlog = "-"`
    - Añadir a `tests/conftest.py` la fixture `client` = `create_app(settings, FakeOcrEngine(...), clock=fixed_clock).test_client()` (sin trabajo en segundo plano: la purga es oportunista)
    - _Requirements: 1.5, 4.6, 10.5, 10.6, 11.5, 11.6, 11.7, 12.1, 12.2, 13.1, 14.7, 16.2, 16.18_

  - [x]* 12.7 Escribir prueba de propiedad de reflejo del Borrador y escapado HTML
    - **Property 9: El formulario de revisión refleja el Borrador y escapa HTML**
    - **Validates: Requirements 4.1, 4.3, 14.7**

  - [x]* 12.8 Escribir prueba de propiedad de recursos inexistentes y rutas fuera del almacén
    - **Property 20: Recursos inexistentes y rutas fuera del almacén responden 404**
    - **Validates: Requirements 8.6, 14.3, 14.4**

  - [x]* 12.9 Escribir pruebas unitarias de las rutas
    - Con `client` (`app.test_client()`) y subidas `multipart/form-data` simuladas con `io.BytesIO`: `GET /upload` con el formulario (`capture`/`accept`, `action="/uploads"`); `base.html` enlaza `/`, `/upload` y `/entries`; cancelar un Borrador → 303 a `/upload`; POST sin fichero, formato no admitido, fichero mayor que `max_bytes` y cuerpo mayor que `MAX_CONTENT_LENGTH` (ambos 413 con el mismo mensaje); imagen y texto en revisión; errores con valores conservados; detalle con mensaje tras guardar; `datalist` de categorías; página de confirmación de borrado; parcial HTMX; cabeceras de seguridad presentes
    - _Requirements: 1.1, 1.5, 1.6, 4.2, 4.5, 5.7, 6.6, 7.2, 9.2, 12.4_

  - [x]* 12.10 Escribir pruebas unitarias de arranque y `/health`
    - Creación de directorios en `create_app`; directorio no escribible → `load_app` lanza `SystemExit(1)` y registra la ruta (en Windows, simular con `monkeypatch` en lugar de permisos POSIX); recrear la app con los mismos directorios conserva los datos; `/health` 200 `ok`, 200 `degraded` con OCR caído, 503 con BD o almacén fallando, respuesta < 2 s
    - `app.wsgi`: con `monkeypatch.setenv` de `INVOICE_DB_PATH` e `INVOICE_IMAGES_DIR` en `tmp_path`, importar (o `importlib.reload`) `app.wsgi` y comprobar que `app` es una instancia de `Flask` y un *callable* WSGI que responde a `GET /health` con su `test_client()`
    - `gunicorn.conf.py`: cargarlo con `runpy.run_path` e `INVOICE_PORT` fijado y comprobar `bind == "0.0.0.0:<INVOICE_PORT>"`, `workers == 2`, `timeout > 60` y `preload_app is True` (no arranca Gunicorn, así que funciona en Windows)
    - _Requirements: 10.6, 11.5, 11.6, 11.7, 12.1, 12.2, 13.1, 13.2, 13.3_

  - [x] 12.11 Implementar la Portada: rutas `GET /` y `GET /recent` y sus plantillas
    - En `app/routes/pages.py`: `GET /` con `recent_page` normalizado por `parse_recent_page`, `DraftPurger.maybe_purge()`, `DashboardService.expense_summary()` y `recent(page)`, render de `home.html`; `GET /recent?page=N` con `HX-Request` → `_recent_rows.html` (filas + `#recent-more` con `hx-swap-oob="outerHTML"`), sin `HX-Request` → `redirect("/?recent_page=N", code=303)`
    - `home.html`: botón destacado "Subir factura" (`/upload`), dos totales anuales, tabla de 12 meses (Enero..Diciembre, año anterior | Año_Actual) con `<caption>` y `<th scope>`, importes con `|eur`; mensaje de estado vacío con enlace a `/upload`
    - `_recent_table.html` y `_recent_rows.html`: columnas fecha de factura, proveedor, concepto, categoría, Tipo, total `|eur` y enlace a `/entries/{id}`; `<tbody id="recent-rows">`; `#recent-more` con "Ver siguientes" (`href="/?recent_page=N+1"`, `hx-get="/recent?page=N+1"`, `hx-target="#recent-rows"`, `hx-swap="beforeend"`) solo si `has_more`, y "Anteriores" si `page > 1`; mensaje "No hay más facturas" con enlace a la página 1 si la página no tiene Apuntes y `page > 1`
    - `static/app.css`: `.table-scroll` con `overflow-x: auto`, filas de Últimas_Facturas como tarjetas apiladas con `data-label` por debajo de 640 px, botón "Ver siguientes" a todo el ancho en móvil
    - _Requirements: 1.1, 16.1, 16.6, 16.7, 16.8, 16.9, 16.10, 16.11, 16.12, 16.13, 16.14, 16.15, 16.16, 16.17, 16.18, 16.19, 14.7_

  - [x]* 12.12 Escribir pruebas unitarias de la Portada
    - En `tests/unit/test_routes_home.py` con `client` y `fixed_clock`: sin Apuntes → invitación a subir y `0,00 €` en 2 totales y 24 celdas; con datos → 12 filas Enero..Diciembre y formato `1.234,56 €`; botón "Subir factura" con `href="/upload"`; 11 Apuntes → 10 filas y "Ver siguientes" con `href="/?recent_page=2"` y `hx-get="/recent?page=2"`; `/?recent_page=2` → 1 fila, "Anteriores" y sin "Ver siguientes"; `GET /recent?page=2` con `HX-Request: true` → solo `<tr>` y `#recent-more` *out-of-band*; sin `HX-Request` → 303 a `/?recent_page=2`; `recent_page=abc`, `0`, `-1` → página 1; `recent_page=99` → "No hay más facturas"; ingresos presentes en la tabla y ausentes del resumen; proveedor con `<script>` escapado; `DraftPurger.maybe_purge` invocado en `GET /`
    - _Requirements: 16.1, 16.5, 16.7, 16.8, 16.9, 16.10, 16.11, 16.12, 16.13, 16.14, 16.15, 16.16, 16.17, 16.18, 14.7_

- [x] 13. Checkpoint: aplicación web completa
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Empaquetado Docker y documentación
  - [x] 14.1 Crear `Dockerfile` y `.dockerignore`
    - `python:3.12.8-slim-bookworm`, `PYTHONPATH=/app`, `tesseract-ocr` + `tesseract-ocr-spa`, usuario `appuser` uid 10001 sin root, `/data/db` y `/data/images` con propietario `appuser`, `COPY gunicorn.conf.py` y `app/`, `ENV INVOICE_PORT=8000`, `HEALTHCHECK` contra `/health`
    - Servidor WSGI: `CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "app.wsgi:app"]` (forma *exec*; equivale a `gunicorn --bind 0.0.0.0:${INVOICE_PORT} --workers 2 --timeout 90 app.wsgi:app`); nunca el servidor de desarrollo de Flask
    - `.dockerignore` excluye `.venv`, `.git`, `tests`, `.kiro`, `coverage.json`, `__pycache__`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.6, 13.4_

  - [x] 14.2 Crear `docker-compose.yml`
    - Comentario de aviso "sin autenticación", `ports` con `${BIND_ADDRESS:-0.0.0.0}`, variables `INVOICE_*`, volúmenes con nombre `invoice-db` e `invoice-images`, `restart: unless-stopped`, sin `network_mode: host`
    - _Requirements: 11.4, 12.1, 12.3, 14.1_

  - [x] 14.3 Redactar `README.md`
    - Sección destacada **⚠️ Sin autenticación**: cualquier dispositivo de la LAN tiene acceso completo; no redirigir el puerto ni exponerlo a Internet; limitar con `BIND_ADDRESS`; inicio de sesión como mejora futura
    - Despliegue con `docker compose up --build`, variables de entorno, volúmenes y `chown 10001` para *bind mounts*
    - Desarrollo en Windows: `py -3.12 -m venv .venv`, `.venv\Scripts\pip install -r requirements-dev.txt`, `.venv\Scripts\python -m pytest` (sin Tesseract), `.venv\Scripts\python -m app.main` (servidor de desarrollo de Flask en `127.0.0.1`; Gunicorn no funciona en Windows y solo se usa en el contenedor), `.venv\Scripts\python tools\check_coverage.py coverage.json --min 80`, `pytest -m tesseract` dentro del contenedor
    - _Requirements: 14.1, 14.2, 15.2, 15.3_

  - [x]* 14.4 Escribir prueba unitaria del aviso del README
    - Comprueba que `README.md` contiene el aviso de ausencia de autenticación y la advertencia sobre exponer el puerto
    - _Requirements: 14.2_

- [x] 15. Checkpoint final: pruebas y cobertura por fichero
  - Ejecutar `.venv\Scripts\python -m pytest` y `.venv\Scripts\python tools\check_coverage.py coverage.json --min 80`; todos los ficheros de `app/` deben alcanzar al menos el 80 %.
  - Ensure all tests pass, ask the user if questions arise.

## Notas

- Las sub-tareas marcadas con `*` son pruebas opcionales y pueden omitirse para un MVP más rápido. Aun así, el umbral de cobertura por fichero (≥ 80 %) del checkpoint final solo se alcanza de forma realista con ellas, así que conviene implementarlas antes de hacer commit.
- Cada propiedad de corrección está en su propio fichero `tests/unit/test_*_properties.py` para que las sub-tareas puedan ejecutarse en paralelo sin conflictos.
- La ejecución por defecto no necesita Tesseract ni red: `FakeOcrEngine`, `pytesseract` simulado y rutas en `tmp_path`.
- Las pruebas manuales de Docker (salud del contenedor, `id -u` ≠ 0, persistencia tras `down`/`up`) y de usabilidad móvil (incluida la Portada a 360 px y "Ver siguientes" con JavaScript real, 16.11 y 16.19) quedan fuera de este plan porque no son tareas de código.
- Portada (Requisito 16): las tareas 1.8–1.12, 8.6–8.8, 10.9–10.11, 12.11 y 12.12 se añadieron al final de sus secciones para no renumerar; 12.2, 12.3, 12.6 y 12.9 se actualizaron por el traslado del formulario de subida a `GET /upload`.
- Ampliación del extractor (Requisitos 3.11–3.15, facturas en tabla y encabezado FACTURA): las tareas 4.11–4.15 se añadieron al final de la sección 4 para no renumerar. 4.11 amplía `app/extractor.py`; las pruebas 4.12–4.14 amplían las Properties 5, 6 y 26–28, y 4.15 añade una prueba unitaria con el Texto_OCR real de Leroy Merlin como fixture (el texto se captura por separado). El resto de módulos no cambia.
- Antes de implementar, conviene revisar si hay ARMs activos sobre Docker, Python/Flask/WSGI y Testing, tal como indica el diseño.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.4", "1.6", "2.1", "3.1"] },
    { "id": 2, "tasks": ["1.3", "1.5", "1.7", "1.8", "1.9", "4.1", "7.1", "8.1", "9.1"] },
    { "id": 3, "tasks": ["1.10", "1.11", "1.12", "2.2", "2.3", "3.2", "4.2", "5.1", "7.2", "7.3", "8.2", "9.2", "9.3"] },
    { "id": 4, "tasks": ["4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "5.2", "5.3", "5.4", "5.5", "8.3", "8.4", "8.5", "8.6", "10.1"] },
    { "id": 5, "tasks": ["4.11", "8.7", "8.8", "10.2"] },
    { "id": 6, "tasks": ["4.12", "4.13", "4.14", "4.15", "10.3", "10.4", "10.5", "10.6", "10.7", "10.8", "10.9", "12.1", "12.2"] },
    { "id": 7, "tasks": ["10.10", "10.11", "12.3", "12.4", "12.5"] },
    { "id": 8, "tasks": ["12.6", "12.11"] },
    { "id": 9, "tasks": ["12.7", "12.8", "12.9", "12.10", "12.12", "14.1", "14.2", "14.3"] },
    { "id": 10, "tasks": ["14.4"] }
  ]
}
```
