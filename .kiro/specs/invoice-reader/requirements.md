# Documento de Requisitos

## Introducción

Invoice Reader es una aplicación web en Python (Flask + plantillas Jinja2 renderizadas en servidor, con HTMX o JavaScript ligero) que permite subir la foto de una factura, extraer sus datos mediante OCR local (Tesseract con el paquete de idioma español `spa`) y reglas/expresiones regulares, revisar y corregir los datos extraídos y guardarlos como un apunte contable simple (gasto o ingreso). La imagen original de la factura se conserva en disco vinculada a su apunte.

La aplicación es de un solo usuario, sin autenticación, usa SQLite como base de datos y se empaqueta como contenedor Docker para ejecutarse en un servidor local de la red doméstica (LAN). Dentro del contenedor, la aplicación se sirve con un servidor WSGI (Gunicorn). Ningún dato sale del servidor: no se usan LLM ni APIs externas.

Notación: los requisitos siguen los patrones EARS con palabras clave en español: CUANDO (WHEN), MIENTRAS (WHILE), SI ... ENTONCES (IF ... THEN), DONDE (WHERE) y DEBERÁ (SHALL).

Supuestos:
- Las facturas están en español y los importes en euros.
- Los formatos de imagen admitidos son JPEG y PNG.

## Glosario

- **Aplicación**: el servicio web Invoice Reader completo (aplicación WSGI Flask que incluye el backend y la interfaz web).
- **Servidor_WSGI**: servidor HTTP compatible con WSGI (Gunicorn) que ejecuta la Aplicación dentro del Contenedor.
- **Interfaz_Web**: conjunto de páginas HTML servidas por la Aplicación y mostradas en el navegador del Usuario.
- **Usuario**: la única persona que usa la Aplicación desde un navegador de la LAN.
- **Factura**: documento fiscal fotografiado que el Usuario sube a la Aplicación.
- **Imagen_Factura**: fichero de imagen (JPEG o PNG) con la foto de una Factura.
- **Motor_OCR**: componente de la Aplicación que obtiene el texto de una Imagen_Factura usando Tesseract con el idioma `spa`.
- **Texto_OCR**: texto plano devuelto por el Motor_OCR para una Imagen_Factura.
- **Extractor**: componente de la Aplicación que obtiene los Campos_Apunte a partir del Texto_OCR mediante reglas y expresiones regulares.
- **Apunte**: registro de gasto o ingreso asociado a exactamente una Factura, con los Campos_Apunte y la referencia a su Imagen_Factura.
- **Campos_Apunte**: fecha, proveedor/emisor, NIF/CIF, número de factura, concepto, categoría, tipo, base imponible, IVA y total.
- **Tipo**: clasificación del Apunte; valores permitidos `gasto` e `ingreso`.
- **Borrador**: conjunto de Campos_Apunte propuestos por el Extractor, pendiente de revisión por el Usuario y todavía no guardado.
- **Base_Datos**: fichero SQLite donde se almacenan los Apuntes.
- **Almacén_Imágenes**: directorio en disco donde se guardan las Imágenes_Factura.
- **Contenedor**: contenedor Docker que ejecuta la Aplicación en el servidor local.
- **Endpoint_Salud**: ruta HTTP `GET /health` que informa del estado de la Aplicación.
- **LAN**: red local de confianza del hogar donde se despliega el servidor.
- **Importe**: valor monetario en euros con dos decimales.
- **Portada**: página principal de la Interfaz_Web, servida en la ruta `/`, con el Resumen_Gastos, la tabla de Últimas_Facturas y el acceso al formulario de subida.
- **Reloj**: fuente de la fecha y hora actuales de la Aplicación, sustituible por un reloj fijo en las pruebas.
- **Año_Actual**: año de la fecha devuelta por el Reloj.
- **Fecha_Alta**: fecha y hora en que la Aplicación guardó el Apunte por primera vez (fecha y hora de creación del Requisito 6.1).
- **Resumen_Gastos**: bloque de la Portada con los totales anuales y el desglose mensual de los Apuntes de Tipo `gasto` del Año_Actual y del año anterior, agrupados por la fecha de factura.
- **Últimas_Facturas**: secuencia de todos los Apuntes, de cualquier Tipo, ordenada por Fecha_Alta descendente y, a igual Fecha_Alta, por identificador descendente.
- **Página_Recientes**: cada grupo consecutivo de hasta 10 Apuntes de Últimas_Facturas, numerado desde 1 (la Página_Recientes 1 contiene los 10 Apuntes con Fecha_Alta más reciente).

## Requisitos

### Requisito 1: Subida de la imagen de la factura

**Historia de usuario:** Como Usuario, quiero subir la foto de una factura desde el navegador, para que la Aplicación la procese y la conserve.

#### Criterios de aceptación

1. LA Interfaz_Web DEBERÁ ofrecer, en la ruta `/upload`, un formulario para seleccionar y subir una Imagen_Factura, enlazado desde la Portada y desde la navegación común de todas las páginas.
2. CUANDO el Usuario sube un fichero JPEG o PNG de tamaño igual o inferior al tamaño máximo configurado, LA Aplicación DEBERÁ aceptar el fichero y enviarlo al Motor_OCR.
3. LA Aplicación DEBERÁ usar un tamaño máximo de subida de 10 MB cuando la variable de entorno de tamaño máximo no esté definida.
4. SI el fichero subido no es JPEG ni PNG según su contenido binario (firma del fichero), ENTONCES LA Aplicación DEBERÁ rechazar el fichero y mostrar un mensaje de error indicando los formatos admitidos.
5. SI el fichero subido supera el tamaño máximo configurado, ENTONCES LA Aplicación DEBERÁ rechazar el fichero y mostrar un mensaje de error indicando el tamaño máximo.
6. SI el Usuario envía el formulario sin fichero, ENTONCES LA Aplicación DEBERÁ mostrar un mensaje de error solicitando un fichero.

### Requisito 2: Reconocimiento óptico de caracteres local

**Historia de usuario:** Como Usuario, quiero que el texto de la factura se lea en mi propio servidor, para que mis datos no salgan de mi red.

#### Criterios de aceptación

1. CUANDO la Aplicación acepta una Imagen_Factura, EL Motor_OCR DEBERÁ obtener el Texto_OCR usando Tesseract con el idioma `spa`.
2. EL Motor_OCR DEBERÁ procesar la Imagen_Factura dentro del Contenedor, sin enviar la imagen ni el texto a servicios externos.
3. SI el Motor_OCR no termina en 60 segundos, ENTONCES LA Aplicación DEBERÁ cancelar el reconocimiento y mostrar un Borrador vacío con un aviso de fallo de OCR.
4. SI Tesseract o el idioma `spa` no están disponibles, ENTONCES LA Aplicación DEBERÁ registrar el error en el log y mostrar un Borrador vacío con un aviso de fallo de OCR.
5. SI el Motor_OCR devuelve un Texto_OCR vacío, ENTONCES LA Aplicación DEBERÁ mostrar un Borrador vacío con un aviso de que no se ha detectado texto.

### Requisito 3: Extracción de campos por reglas

**Historia de usuario:** Como Usuario, quiero que la Aplicación proponga los datos del apunte a partir del texto leído, para ahorrarme teclearlos.

#### Criterios de aceptación

1. CUANDO el Motor_OCR devuelve un Texto_OCR no vacío, EL Extractor DEBERÁ generar un Borrador con los Campos_Apunte detectados.
2. EL Extractor DEBERÁ reconocer fechas en los formatos `dd/mm/aaaa`, `dd-mm-aaaa`, `dd.mm.aaaa` y `dd/mm/aa` y convertirlas a fecha ISO `aaaa-mm-dd`.
3. EL Extractor DEBERÁ reconocer NIF (8 dígitos y letra), NIE (letra X, Y o Z, 7 dígitos y letra) y CIF (letra, 7 dígitos y carácter de control) con o sin guiones o espacios, y normalizarlos a mayúsculas sin separadores.
4. EL Extractor DEBERÁ reconocer Importes en formato español (`1.234,56`) y en formato con punto decimal (`1234.56`), con o sin símbolo `€`, y convertirlos a valores decimales con dos decimales.
5. EL Extractor DEBERÁ asignar la base imponible, el IVA y el total a partir de las etiquetas habituales (por ejemplo "Base imponible", "IVA", "Total", "Total factura", "Importe total").
6. EL Extractor DEBERÁ obtener el número de factura a partir de etiquetas como "Factura nº", "Nº factura", "Número de factura" o "Invoice".
7. EL Extractor DEBERÁ proponer como proveedor/emisor la primera línea no vacía del Texto_OCR que no sea una fecha, un NIF/CIF ni un Importe.
8. SI el Extractor no detecta un Campo_Apunte, ENTONCES EL Extractor DEBERÁ dejar ese campo vacío en el Borrador y marcarlo como no detectado.
9. EL Extractor DEBERÁ proponer el Tipo `gasto` por defecto en el Borrador.
10. EL Extractor DEBERÁ ser una función determinista: para el mismo Texto_OCR DEBERÁ producir el mismo Borrador.
11. CUANDO una línea del Texto_OCR es un encabezado "FACTURA" seguido de un número (sin "nº"), por ejemplo `FACTURA 050-0008-327711`, EL Extractor DEBERÁ proponer ese número como número de factura, admitiendo tokens formados por dígitos, letras, guiones, barras y puntos, y conservando además el reconocimiento de las etiquetas "Factura nº", "Nº factura", "Número de factura" e "Invoice" del criterio 3.6.
12. EL Extractor DEBERÁ reconocer la etiqueta "Fecha de venta" como ancla de fecha adicional, tomando como fecha de factura la primera fecha válida que aparezca tras ella, y DEBERÁ seguir reconociendo la etiqueta "Fecha" y, en su defecto, la primera fecha válida del Texto_OCR (criterio 3.2).
13. CUANDO una etiqueta de base imponible, IVA o total no lleva ningún Importe en su propia línea, EL Extractor DEBERÁ buscar el Importe en la siguiente línea no vacía del Texto_OCR, para admitir facturas en tabla con una fila de etiquetas sobre una fila de números.
14. EL Extractor DEBERÁ reconocer, además de las etiquetas del criterio 3.5, los sinónimos de etiqueta habituales en facturas españolas: para la base imponible "Total si" y "Base imponible (EUR)"; para el IVA "Total IVA", "Total IVA/IGIC/IPSI" y "Cuota"; para el total "Total TII", "Total (EUR)" e "Importe total (EUR)"; y DEBERÁ excluir del IVA las columnas de porcentaje, incluida una columna "Tasa IVA/IGIC/IPSI" cuyo valor sea un tipo impositivo (por ejemplo `21,00`), tomando como IVA el Importe de la columna "Total IVA...".
15. EL Extractor DEBERÁ tratar las facturas en formato de tabla o de varias columnas con criterio de mejor esfuerzo: cuando el Texto_OCR aplana la tabla de forma que un Campo_Apunte no se puede asignar sin ambigüedad, EL Extractor DEBERÁ dejar ese campo sin detectar, quedando editable por el Usuario en la revisión (criterio 3.8 y Requisitos 4 y 5).

### Requisito 4: Revisión y edición antes de guardar

**Historia de usuario:** Como Usuario, quiero revisar y corregir los datos extraídos antes de guardarlos, para que el apunte sea correcto aunque el OCR falle.

#### Criterios de aceptación

1. CUANDO se genera un Borrador, LA Interfaz_Web DEBERÁ mostrar un formulario editable con todos los Campos_Apunte rellenados con los valores del Borrador.
2. MIENTRAS el Usuario revisa un Borrador, LA Interfaz_Web DEBERÁ mostrar la Imagen_Factura y el Texto_OCR junto al formulario.
3. MIENTRAS el Usuario revisa un Borrador, LA Interfaz_Web DEBERÁ resaltar los Campos_Apunte marcados como no detectados.
4. LA Aplicación DEBERÁ guardar un Apunte únicamente cuando el Usuario confirma el formulario de revisión.
5. CUANDO el Usuario cancela la revisión de un Borrador, LA Aplicación DEBERÁ descartar el Borrador y eliminar la Imagen_Factura temporal asociada.
6. LA Aplicación DEBERÁ eliminar las Imágenes_Factura temporales de Borradores no confirmados con una antigüedad superior a 24 horas.

### Requisito 5: Validación de los datos del apunte

**Historia de usuario:** Como Usuario, quiero que la Aplicación valide los datos antes de guardarlos, para evitar apuntes incoherentes.

#### Criterios de aceptación

1. LA Aplicación DEBERÁ exigir los campos fecha, Tipo y total para guardar un Apunte.
2. SI la fecha no es una fecha de calendario válida, ENTONCES LA Aplicación DEBERÁ rechazar el guardado y mostrar un error junto al campo fecha.
3. SI el Tipo no es `gasto` ni `ingreso`, ENTONCES LA Aplicación DEBERÁ rechazar el guardado y mostrar un error junto al campo Tipo.
4. SI la base imponible, el IVA o el total no son Importes numéricos mayores o iguales que cero, ENTONCES LA Aplicación DEBERÁ rechazar el guardado y mostrar un error junto a cada campo inválido.
5. SI la base imponible y el IVA están informados y su suma difiere del total en más de 0,01 €, ENTONCES LA Aplicación DEBERÁ mostrar un aviso de descuadre y permitir guardar el Apunte tras la confirmación del Usuario.
6. SI el NIF/CIF informado no supera la validación de formato y dígito de control, ENTONCES LA Aplicación DEBERÁ mostrar un aviso junto al campo NIF/CIF y permitir guardar el Apunte.
7. CUANDO la Aplicación rechaza un guardado por validación, LA Interfaz_Web DEBERÁ conservar los valores introducidos por el Usuario en el formulario.

### Requisito 6: Guardado del apunte y de la imagen

**Historia de usuario:** Como Usuario, quiero que cada factura quede guardada como apunte junto con su foto original, para poder consultarla más adelante.

#### Criterios de aceptación

1. CUANDO el Usuario confirma un Borrador válido, LA Aplicación DEBERÁ guardar un Apunte en la Base_Datos con los Campos_Apunte, el Texto_OCR y la fecha y hora de creación.
2. CUANDO la Aplicación guarda un Apunte, LA Aplicación DEBERÁ mover la Imagen_Factura al Almacén_Imágenes con un nombre de fichero único generado por la Aplicación (UUID) y la extensión correspondiente a su formato.
3. LA Aplicación DEBERÁ conservar la Imagen_Factura con contenido idéntico, byte a byte, al fichero subido.
4. LA Aplicación DEBERÁ asociar cada Apunte a exactamente una Imagen_Factura.
5. SI falla la escritura de la Imagen_Factura o del Apunte, ENTONCES LA Aplicación DEBERÁ deshacer la operación completa, de forma que no quede un Apunte sin imagen ni una imagen sin Apunte, y mostrar un mensaje de error.
6. CUANDO la Aplicación guarda un Apunte correctamente, LA Interfaz_Web DEBERÁ mostrar la página de detalle del Apunte con un mensaje de confirmación.

### Requisito 7: Categorías

**Historia de usuario:** Como Usuario, quiero clasificar los apuntes por categoría, para organizar mis gastos e ingresos.

#### Criterios de aceptación

1. LA Aplicación DEBERÁ ofrecer una lista inicial de categorías: Suministros, Alimentación, Transporte, Hogar, Salud, Ocio, Servicios profesionales, Impuestos y Otros.
2. LA Interfaz_Web DEBERÁ permitir seleccionar una categoría existente o escribir una categoría nueva en el formulario del Apunte.
3. CUANDO el Usuario guarda un Apunte con una categoría nueva, LA Aplicación DEBERÁ incorporar la categoría a las sugerencias de categorías.

### Requisito 8: Consulta de apuntes

**Historia de usuario:** Como Usuario, quiero ver y filtrar mis apuntes, para controlar mis gastos e ingresos.

#### Criterios de aceptación

1. LA Interfaz_Web DEBERÁ mostrar un listado de Apuntes ordenado por fecha de factura descendente con fecha, proveedor/emisor, concepto, categoría, Tipo y total.
2. LA Interfaz_Web DEBERÁ paginar el listado de Apuntes con 20 Apuntes por página e indicar la página actual, el tamaño de página y el número total de Apuntes.
3. CUANDO el Usuario aplica filtros por Tipo, categoría o rango de fechas, LA Aplicación DEBERÁ mostrar únicamente los Apuntes que cumplen todos los filtros aplicados.
4. MIENTRAS hay filtros aplicados, LA Interfaz_Web DEBERÁ mostrar la suma de totales de gastos y la suma de totales de ingresos de los Apuntes filtrados.
5. CUANDO el Usuario abre un Apunte, LA Interfaz_Web DEBERÁ mostrar todos los Campos_Apunte, el Texto_OCR y la Imagen_Factura asociada.
6. SI el Usuario solicita un Apunte o una Imagen_Factura inexistente, ENTONCES LA Aplicación DEBERÁ responder con el código HTTP 404 y una página de error.

### Requisito 9: Edición y eliminación de apuntes

**Historia de usuario:** Como Usuario, quiero modificar o borrar apuntes ya guardados, para corregir errores.

#### Criterios de aceptación

1. CUANDO el Usuario edita un Apunte guardado y confirma los cambios, LA Aplicación DEBERÁ aplicar las validaciones del Requisito 5 y actualizar el Apunte en la Base_Datos.
2. CUANDO el Usuario solicita eliminar un Apunte, LA Interfaz_Web DEBERÁ pedir confirmación explícita antes de eliminarlo.
3. CUANDO el Usuario confirma la eliminación de un Apunte, LA Aplicación DEBERÁ eliminar el Apunte de la Base_Datos y su Imagen_Factura del Almacén_Imágenes.
4. SI la Imagen_Factura de un Apunte eliminado no existe en el Almacén_Imágenes, ENTONCES LA Aplicación DEBERÁ registrar un aviso en el log y completar la eliminación del Apunte.

### Requisito 10: Empaquetado en contenedor Docker

**Historia de usuario:** Como Usuario, quiero desplegar la Aplicación como contenedor Docker en mi servidor local, para no ejecutarla en mi PC.

#### Criterios de aceptación

1. EL proyecto DEBERÁ incluir un `Dockerfile` basado en una imagen oficial de Python en variante `slim` con versión fijada.
2. EL `Dockerfile` DEBERÁ instalar Tesseract y el paquete de idioma `tesseract-ocr-spa` en la imagen.
3. EL `Dockerfile` DEBERÁ ejecutar la Aplicación con un usuario del sistema sin privilegios de root.
4. EL proyecto DEBERÁ incluir un fichero `docker-compose.yml` que construya la imagen, publique el puerto de la Aplicación y monte el almacenamiento persistente.
5. CUANDO se ejecuta `docker compose up` en un servidor con Docker, EL Contenedor DEBERÁ arrancar la Aplicación sin pasos manuales adicionales, creando el esquema de la Base_Datos si no existe.
6. EL Contenedor DEBERÁ servir la Aplicación con un Servidor_WSGI (Gunicorn) que escuche en `0.0.0.0` y en el puerto definido por la variable de entorno `INVOICE_PORT`.

### Requisito 11: Persistencia y configuración

**Historia de usuario:** Como Usuario, quiero que mis datos y fotos sobrevivan a reinicios y actualizaciones del contenedor, para no perder información.

#### Criterios de aceptación

1. LA Aplicación DEBERÁ leer la ruta de la Base_Datos de una variable de entorno, con valor por defecto `/data/db/invoices.db`.
2. LA Aplicación DEBERÁ leer la ruta del Almacén_Imágenes de una variable de entorno, con valor por defecto `/data/images`.
3. LA Aplicación DEBERÁ leer el tamaño máximo de subida de una variable de entorno.
4. EL fichero `docker-compose.yml` DEBERÁ montar `/data/db` y `/data/images` en volúmenes Docker o bind mounts del servidor.
5. CUANDO el Contenedor se recrea con los mismos volúmenes, LA Aplicación DEBERÁ mostrar los Apuntes y las Imágenes_Factura guardados previamente.
6. CUANDO la Aplicación arranca y los directorios de la Base_Datos o del Almacén_Imágenes no existen, LA Aplicación DEBERÁ crearlos.
7. SI la Aplicación no puede escribir en el directorio de la Base_Datos o del Almacén_Imágenes al arrancar, ENTONCES LA Aplicación DEBERÁ registrar un error en el log indicando la ruta afectada y finalizar con un código de salida distinto de cero.

### Requisito 12: Acceso por red

**Historia de usuario:** Como Usuario, quiero acceder a la Aplicación desde cualquier dispositivo de mi red local, para subir facturas desde el móvil o el PC.

#### Criterios de aceptación

1. DENTRO del Contenedor, LA Aplicación DEBERÁ escuchar en la dirección `0.0.0.0`.
2. LA Aplicación DEBERÁ leer el puerto interno de escucha de una variable de entorno, con valor por defecto `8000`.
3. EL fichero `docker-compose.yml` DEBERÁ permitir configurar el puerto publicado en el servidor mediante una variable de entorno.
4. LA Interfaz_Web DEBERÁ ser utilizable en pantallas de móvil de 360 px de ancho y en pantallas de escritorio, incluyendo la subida de fotos desde la cámara del móvil.

### Requisito 13: Comprobación de salud

**Historia de usuario:** Como Usuario, quiero que Docker sepa si la Aplicación funciona, para detectar y reiniciar un contenedor defectuoso.

#### Criterios de aceptación

1. CUANDO se recibe una petición `GET /health`, EL Endpoint_Salud DEBERÁ responder en menos de 2 segundos con un cuerpo JSON que incluya el estado de la Base_Datos, del Almacén_Imágenes y del Motor_OCR.
2. MIENTRAS la Base_Datos es accesible y el Almacén_Imágenes admite escritura, EL Endpoint_Salud DEBERÁ responder con el código HTTP 200.
3. SI la Base_Datos no es accesible o el Almacén_Imágenes no admite escritura, ENTONCES EL Endpoint_Salud DEBERÁ responder con el código HTTP 503.
4. EL `Dockerfile` o el `docker-compose.yml` DEBERÁ definir un `HEALTHCHECK` que invoque el Endpoint_Salud.

### Requisito 14: Seguridad y restricción de despliegue

**Historia de usuario:** Como Usuario, quiero conocer los límites de seguridad de la Aplicación, para desplegarla de forma segura en casa.

#### Criterios de aceptación

1. LA Aplicación DEBERÁ desplegarse exclusivamente en una LAN de confianza, sin exposición a Internet (restricción de despliegue: la Aplicación no tiene autenticación).
2. EL fichero README DEBERÁ documentar de forma explícita que la Aplicación carece de autenticación, que el puerto publicado no debe redirigirse desde el router ni exponerse a Internet, y que un inicio de sesión simple es una mejora futura prevista.
3. LA Aplicación DEBERÁ servir las Imágenes_Factura únicamente mediante el identificador del Apunte o el nombre generado por la Aplicación, resolviendo siempre rutas dentro del Almacén_Imágenes.
4. SI una petición intenta acceder a una ruta fuera del Almacén_Imágenes, ENTONCES LA Aplicación DEBERÁ responder con el código HTTP 404.
5. LA Aplicación DEBERÁ ignorar el nombre de fichero proporcionado por el cliente al almacenar una Imagen_Factura.
6. LA Aplicación DEBERÁ acceder a la Base_Datos mediante consultas parametrizadas.
7. LA Interfaz_Web DEBERÁ escapar en HTML todos los valores procedentes del Texto_OCR y de los Campos_Apunte.

### Requisito 15: Calidad, dependencias y pruebas

**Historia de usuario:** Como desarrollador, quiero un proyecto con dependencias fijadas y pruebas fiables, para mantenerlo con confianza.

#### Criterios de aceptación

1. EL proyecto DEBERÁ fijar versiones exactas de todas las dependencias de Python, sin rangos abiertos.
2. EL proyecto DEBERÁ incluir pruebas unitarias ejecutables con un único comando que no requieran Tesseract instalado, sustituyendo el Motor_OCR por un doble de prueba.
3. EL proyecto DEBERÁ alcanzar una cobertura de pruebas igual o superior al 80 % en cada fichero de código fuente de la Aplicación.
4. LAS pruebas DEBERÁN usar una Base_Datos y un Almacén_Imágenes temporales, sin depender de servicios externos ni de la red.
5. LAS pruebas de integración que requieran Tesseract real DEBERÁN estar marcadas para ejecución manual y excluidas de la ejecución por defecto.

### Requisito 16: Portada con resumen de gastos y últimas facturas

**Historia de usuario:** Como Usuario, quiero una portada con el resumen de mis gastos de este año y del anterior y con las últimas facturas que he dado de alta, para ver de un vistazo cuánto gasto y qué he registrado recientemente.

#### Criterios de aceptación

1. CUANDO el Usuario solicita la ruta `/`, LA Interfaz_Web DEBERÁ mostrar la Portada con el Resumen_Gastos, la tabla de Últimas_Facturas y un botón "Subir factura" que enlaza con el formulario de subida de la ruta `/upload`.
2. LA Aplicación DEBERÁ tomar como Año_Actual el año de la fecha devuelta por el Reloj y como año anterior el Año_Actual menos uno.
3. LA Portada DEBERÁ mostrar el total anual de gastos del Año_Actual, calculado como la suma de los totales de todos los Apuntes de Tipo `gasto` cuya fecha de factura pertenece al Año_Actual.
4. LA Portada DEBERÁ mostrar el total anual de gastos del año anterior, calculado como la suma de los totales de todos los Apuntes de Tipo `gasto` cuya fecha de factura pertenece al año anterior.
5. LA Aplicación DEBERÁ excluir los Apuntes de Tipo `ingreso` de todos los importes del Resumen_Gastos.
6. LA Portada DEBERÁ mostrar una tabla de desglose mensual con una fila por cada mes, de Enero a Diciembre, y una columna para el año anterior y otra para el Año_Actual, en la que cada celda contiene la suma de los totales de los Apuntes de Tipo `gasto` cuya fecha de factura pertenece a ese mes y a ese año.
7. SI un mes de un año del Resumen_Gastos no tiene Apuntes de Tipo `gasto`, ENTONCES LA Portada DEBERÁ mostrar `0,00 €` en la celda correspondiente.
8. LA Portada DEBERÁ mostrar en la tabla de Últimas_Facturas la Página_Recientes 1, que incluye Apuntes de Tipo `gasto` y de Tipo `ingreso`.
9. LA tabla de Últimas_Facturas DEBERÁ mostrar para cada Apunte la fecha de factura, el proveedor/emisor, el concepto, la categoría, el Tipo, el total y un enlace a la página de detalle del Apunte.
10. MIENTRAS existan en Últimas_Facturas Apuntes posteriores al último Apunte mostrado, LA Portada DEBERÁ mostrar un botón "Ver siguientes".
11. CUANDO el Usuario pulsa "Ver siguientes" en un navegador con JavaScript activo, LA Interfaz_Web DEBERÁ añadir a la tabla de Últimas_Facturas los Apuntes de la Página_Recientes siguiente sin recargar la página completa.
12. MIENTRAS JavaScript no está disponible en el navegador, LA Interfaz_Web DEBERÁ ofrecer "Ver siguientes" como un enlace que carga la Portada con la Página_Recientes siguiente.
13. MIENTRAS la Portada muestra una Página_Recientes distinta de la Página_Recientes 1, LA Interfaz_Web DEBERÁ ofrecer un enlace "Anteriores" que carga la Portada con la Página_Recientes anterior.
14. SI no existen en Últimas_Facturas Apuntes posteriores al último Apunte mostrado, ENTONCES LA Portada DEBERÁ ocultar el botón "Ver siguientes".
15. SI el número de Página_Recientes solicitado no es un número entero entre 1 y 100000, ENTONCES LA Aplicación DEBERÁ mostrar la Página_Recientes 1.
16. SI el número de Página_Recientes solicitado es mayor que el de la última Página_Recientes con Apuntes, ENTONCES LA Portada DEBERÁ mostrar un mensaje indicando que no hay más facturas y un enlace a la Página_Recientes 1.
17. SI la Base_Datos no contiene Apuntes, ENTONCES LA Portada DEBERÁ mostrar un mensaje que invita a subir la primera factura con un enlace al formulario de subida, y mostrar `0,00 €` en todos los importes del Resumen_Gastos.
18. LA Portada DEBERÁ mostrar los Importes en formato español, con punto como separador de miles, coma decimal, dos decimales y el símbolo `€` precedido de un espacio (por ejemplo, `1.234,56 €`).
19. LA Portada DEBERÁ ser utilizable en pantallas de móvil de 360 px de ancho y en pantallas de escritorio, en las mismas condiciones que el criterio 12.4.
