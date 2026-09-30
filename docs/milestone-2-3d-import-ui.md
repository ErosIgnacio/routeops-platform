# Entrega 2.3d: interfaz de importación y aceptación

## Flujo local

Abrir <http://127.0.0.1:5173/imports>. La página permite crear o seleccionar un
escenario, descargar las cinco plantillas CSV o el libro XLSX, cargar exactamente
cinco CSV con nombres de dataset o un XLSX, fijar el contexto y consultar la
validación recuperable. El área opcional se indica con los límites oeste, sur,
este y norte de un rectángulo; la página lo convierte en `MultiPolygon`. No hay
editor cartográfico. Los horarios de horizonte se envían con un desfase UTC
explícito y se interpretan como instantes; los horarios locales del contenido se
interpretan con la zona IANA elegida. El usuario debe elegir un desfase que
corresponda a la fecha y zona deseadas. El servidor decide la validez final.

El informe separa cantidades aceptadas y rechazadas por dataset, reglas
comprobadas, reglas diferidas e incidencias paginadas con severidad, dataset,
fila, campo y código. `VALID` significa que el paquete pasó validación; solo
`PUBLISHED` significa que existe una revisión inmutable. La página muestra el
bloqueo de publicación por estado, expiración, datos inválidos, paquete vacío o
inventario sin instante de snapshot. La API comprueba de nuevo todas las
condiciones al publicar. El historial permite abrir el detalle de cada revisión.

Se guarda en `localStorage` únicamente el identificador del escenario y lote,
la clave aleatoria de carga y el contexto del formulario. Los originales no se
guardan allí. La clave se crea antes de enviar la carga y se reutiliza al
reintentar la *misma* carga. Si se pierde la respuesta, `GET imports/lookup`
recupera el lote. «Nueva carga» borra esa clave para dar identidad nueva a otro
paquete. Tras recargar se consultan lote, validación, incidencias y publicación.
La validación usa el contexto inmutable como clave semántica; un contexto
distinto exige otra carga. El botón «Reintentar publicación» usa la operación
idempotente y recupera la misma revisión. Se desactivan envíos simultáneos desde
la página. No se muestran porcentajes de progreso: el backend informa estados,
no un porcentaje medible.

Los originales siguen en el volumen privado; no existe endpoint de descarga.
Compose publica frontend, API y PostgreSQL solo en `127.0.0.1`. Este flujo no
incluye reservas ni optimización de revisiones importadas. El tablero y mapa
demo permanecen en `/`.

## API añadida para la interfaz

| Método | Ruta | Función |
| --- | --- | --- |
| `GET` | `/api/v1/scenarios?offset=&limit=` | Escenarios paginados |
| `GET` | `/api/v1/import-templates/{dataset}` | Plantilla CSV de un dataset |
| `GET` | `/api/v1/import-templates/workbook` | Libro XLSX con cinco hojas |
| `GET` | `/api/v1/scenarios/{id}/imports?offset=&limit=` | Lotes recientes paginados |
| `GET` | `/api/v1/scenarios/{id}/imports/lookup?key=` | Recuperar carga por clave idempotente |
| `GET` | `/api/v1/scenarios/{id}/imports/{batch_id}/publication` | Recuperar publicación confirmada |

La carga, validación, incidencias, publicación y revisiones usan los endpoints
documentados en las entregas 2.3a–2.3c y en OpenAPI local. Las listas no
incluyen claves físicas, rutas privadas ni contenido de originales.

## Medición temprana

`backend/scripts/benchmark_import_flow.py` genera paquetes sintéticos, crea una
base PostgreSQL/PostGIS descartable, ejecuta almacenamiento privado, carga,
validación, reproducción verificada y publicación, y elimina la base al salir.
Todos los triggers y restricciones estuvieron activos. En cada paquete hubo un
pedido, una línea, un CD y un vehículo; la columna «inventario» indica sus
filas. Se midió en Docker Desktop con 16 CPU lógicas y 15,44 GiB asignados.
Los tiempos son segundos de pared. La carga medida es la escritura por gateway
y el registro SQL, sin transporte HTTP ni generación de archivos. La memoria es
el máximo RSS muestreado del proceso Python y la memoria muestreada del
contenedor PostgreSQL mediante `docker stats`. La cifra de PostgreSQL incluye
actividad y caché compartidas del contenedor, por lo que no es consumo
incremental atribuible a un paquete. Cada muestra es una sola ejecución.

| Formato | Inventario | Tamaño del paquete | Carga | Validación | Reproducción | Inserción/commit | RSS Python | PostgreSQL muestreado |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| CSV | 10 | 1.239 B | 0,040 | 0,055 | 0,033 | 0,050 | 90,1 MiB | 218,4 MiB |
| CSV | 1.000 | 50.739 B | 0,046 | 0,073 | 0,052 | 0,139 | 99,8 MiB | 227,9 MiB |
| CSV | 10.000 | 500.739 B | 0,057 | 0,165 | 0,226 | 1,008 | 104,0 MiB | 114,5 MiB |
| CSV | 240.000 | 12.000.739 B | 0,078 | 2,823 | 5,826 | 22,218 | 306,6 MiB | 214,1 MiB |
| CSV | 240.000 (SKU ancho 40) | 20.160.773 B | 0,092 | 2,507 | 4,707 | 20,650 | 360,5 MiB | 222,7 MiB |
| XLSX | 1.000 | 26.218 B | 0,041 | 0,102 | 0,084 | 0,141 | 92,9 MiB | 116,6 MiB |
| XLSX | 10.000 | 224.726 B | 0,038 | 0,632 | 0,723 | 0,939 | 99,2 MiB | 116,4 MiB |
| XLSX | 160.000 | 3.555.970 B | 0,041 | 10,544 | 13,316 | 13,966 | 209,1 MiB | 203,2 MiB |

El CSV ancho quedó al 96,1 % del límite de 20 MiB para el archivo de
inventario y al 96 % del límite de 250.000 filas. El XLSX grande tuvo cerca
de 960.000 celdas de inventario, próximo al límite de un millón. La inserción
transaccional por lotes de 500 filas mantuvo la procedencia y la inmutabilidad
existentes. Ninguna muestra falló ni requirió reducir los triggers. Estos datos
no demuestran un máximo de capacidad ni latencia HTTP bajo concurrencia; faltan
mediciones repetidas, tráfico real y hardware objetivo antes de fijar SLO.

Ejemplo de ejecución local con `ROUTEOPS_TEST_DATABASE_URL` apuntando a una
base administrativa de prueba:

```powershell
cd backend
$env:PYTHONPATH = 'src'
python scripts/benchmark_import_flow.py --format csv --inventory-rows 240000 --sku-width 40 --sample-docker
python scripts/benchmark_import_flow.py --format xlsx --inventory-rows 160000 --sku-width 6 --sample-docker
```

## Aceptación visual

Se recorrió en navegador real el escenario local: CSV válido, reporte `VALID`,
recuperación tras recarga, publicación y revisión histórica; XLSX válido;
incidencias de un paquete inválido; y reintento idempotente de publicación.
El detalle de pruebas automatizadas y del smoke queda en el informe de cierre
de esta entrega.
