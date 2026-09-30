# Entrega 2.3a: carga provisional y originales privados

Esta entrega recibe y conserva originales; no valida el contenido semántico, no
publica revisiones y no ejecuta rutas. Los servicios publicados por Compose
escuchan solo en `127.0.0.1`. No se debe exponer RouteOps a la red antes de
incorporar autenticación y autorización.

## API local

| Método | Ruta | Resultado |
|---|---|---|
| `POST` | `/api/v1/scenarios` | Crea un escenario activo; JSON `{"name":"..."}`; `201` |
| `POST` | `/api/v1/scenarios/{scenario_id}/imports` | Recibe `multipart/form-data` con partes `files`; `201` nuevo o `200` reintento idéntico |
| `GET` | `/api/v1/scenarios/{scenario_id}/imports/{batch_id}` | Metadatos, hashes, tamaños y estado; nunca devuelve originales ni claves físicas |

La carga requiere `Idempotency-Key` de 1 a 200 caracteres ASCII
alfanuméricos, `._-`. Envíe exactamente `orders.csv`, `order_lines.csv`,
`inventory.csv`, `distribution_centers.csv` y `vehicles.csv`, o un único archivo
con extensión `.xlsx`. Las partes pueden llegar en cualquier orden y deben
llamarse `files`. Para XLSX se registra un solo `import_files` con dataset
`workbook`; para CSV se registran cinco. La aceptación de la carga solo
significa que los originales se recibieron con límites y metadatos correctos:
el estado es `RECEIVED`, no `VALID`.

Ejemplo PowerShell (el escenario se crea antes):

```powershell
$scenario = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/v1/scenarios `
  -ContentType application/json -Body '{"name":"Mi escenario"}'
$key = [guid]::NewGuid().ToString()
curl.exe -X POST "http://localhost:8000/api/v1/scenarios/$($scenario.id)/imports" `
  -H "Idempotency-Key: $key" `
  -F "files=@orders.csv" -F "files=@order_lines.csv" `
  -F "files=@inventory.csv" -F "files=@distribution_centers.csv" `
  -F "files=@vehicles.csv"
```

Los rechazos usan un `code` estable y mensaje genérico. `413` indica límite
por archivo o paquete; `422`, combinación o formato no admitido; `409`, una
clave de idempotencia reutilizada con otro contenido o un lote expirado. El
hash de paquete se calcula sobre dataset, SHA-256 y tamaño de cada archivo en
orden de dataset, así que el orden de las partes no altera el resultado.
Una carga interrumpida no genera lote. Los originales no están disponibles
mediante HTTP.

La API presenta `status: EXPIRED` cuando vence el plazo, incluso si la tarea
todavía no registra la expiración. `expires_at` muestra el plazo y `expired_at`
la fecha del registro auditable. Un reintento con esa clave recibe
`BATCH_EXPIRED`.

## Almacenamiento y mantenimiento

`ObjectStorageGateway` abstrae la lectura y escritura. Compose comparte el
volumen privado `import-originals` entre API y `import-maintenance`; ninguna
ruta del volumen se monta en el frontend. Se escriben archivos temporales con
clave aleatoria, se calculan SHA-256 y tamaño durante la recepción, y se
incorporan al área de originales antes de la transacción SQL. La transacción
registra lote, evento inicial y archivos juntos. Un fallo SQL o un reintento
elimina los objetos nuevos. Si el proceso cae antes de esa limpieza, la tarea
de mantenimiento recupera los temporales y originales sin referencia una vez
transcurrido el plazo de gracia.

| Variable | Predeterminado | Uso |
|---|---:|---|
| `ROUTEOPS_IMPORT_STORAGE_ROOT` | `/app/private-imports` | Directorio privado dentro del backend; Compose lo fija al volumen |
| `ROUTEOPS_IMPORT_MAX_FILE_BYTES` | 20 MiB | Límite aplicado durante recepción |
| `ROUTEOPS_IMPORT_MAX_PACKAGE_BYTES` | 100 MiB | Suma de archivos, aplicada durante recepción |
| `ROUTEOPS_IMPORT_RETENTION_DAYS` | 30 | Expiración desde la última transición del lote no publicado |
| `ROUTEOPS_IMPORT_ORPHAN_GRACE_SECONDS` | 3600 | Antigüedad mínima para barrer objetos sin referencia |
| `ROUTEOPS_IMPORT_MAINTENANCE_INTERVAL_SECONDS` | 3600 | Frecuencia del servicio recuperable |

La tarea ejecuta `python -m routeops.infrastructure.storage.maintenance`
una vez o con `--loop`. Al expirar un lote se añade una fila a
`import_batch_expirations`; después se borran sus originales y se registra cada
borrado en `import_file_deletions`. Una interrupción entre estos pasos se
resuelve en la siguiente ejecución. Se conservan el lote, hashes, archivos
metadatos, eventos e incidencias sin modificar las tablas inmutables de 2.2.
Las futuras publicaciones deberán comprobar que no existe expiración antes
de iniciar su transacción y que el plazo no haya vencido aunque la tarea de
limpieza todavía no se haya ejecutado. Los originales de revisiones publicadas
no expiran.

Los límites de filas, celdas, expansión XLSX y validaciones del contrato 2.1
se aplicarán al procesar el paquete en 2.3b. La coordinación durable de esa
validación mediante PostgreSQL, concesiones, heartbeat y reintentos también
corresponde a 2.3b; no forma parte de la carga provisional. La configuración
local de puertos es una protección temporal de desarrollo, no un sistema de
control de acceso.
