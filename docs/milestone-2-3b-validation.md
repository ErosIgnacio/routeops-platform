# Entrega 2.3b: validación contextual recuperable

Esta fase valida los originales privados de 2.3a. No publica revisiones, no
reserva inventario, no ejecuta VROOM y no incorpora una interfaz de importación.
Los puertos de Compose siguen vinculados a `127.0.0.1`.

## API local

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/api/v1/scenarios/{scenario_id}/imports/{batch_id}/validation` | `202` mientras trabaja, `200` si ya terminó; mismo contexto devuelve el trabajo o resultado existente; contexto diferente devuelve `409 VALIDATION_CONTEXT_CONFLICT` |
| `GET` | `/api/v1/scenarios/{scenario_id}/imports/{batch_id}/validation` | Estado, huellas, intentos y resumen del informe |
| `GET` | `/api/v1/scenarios/{scenario_id}/imports/{batch_id}/validation/issues?after=0&limit=100` | Incidencias ordenadas por `ordinal`; `limit` de 1 a 200 y `next_after` para la página siguiente |

Ejemplo de cuerpo JSON para `POST`:

```json
{
  "planning_date": "2026-10-15",
  "horizon_start_at": "2026-10-15T08:00:00-03:00",
  "horizon_end_at": "2026-10-15T20:00:00-03:00",
  "timezone_iana": "America/Santiago",
  "currency": "CLP",
  "operational_area": null,
  "contract_version": "2.1",
  "validator_version": "2.3b.1"
}
```

`operational_area` puede ser un GeoJSON `MultiPolygon` con coordenadas
`[longitud, latitud]`, SRID 4326. Se comprueba su validez con PostGIS, hasta
10 000 vértices. Es opcional. Un punto sobre el límite está cubierto; un punto
fuera genera `WARNING POINT_OUTSIDE_AREA`. La respuesta nunca contiene claves
del almacenamiento ni rutas físicas. Los mensajes y extractos de incidencias
siguen la política de omisión de contenido de 2.1.

Los instantes de pedidos e inventario exigen offset explícito: representan
instantes absolutos y se convierten a la zona del escenario para comparar con
la jornada. Los horarios de centros y vehículos son horas locales de
`planning_date` en `timezone_iana`; se rechazan horas inexistentes y ambiguas
en cambios de horario. Se comprueba que las ventanas de pedidos y los horarios
queden dentro del horizonte de una única fecha local. El fin de un horario
puede coincidir con el fin exclusivo del horizonte. El inventario debe
compartir un único `snapshot_at`. Se reutilizan los códigos de 2.1 y se añaden
`WINDOW_OUTSIDE_HORIZON`, `LOCAL_TIME_NONEXISTENT`, `LOCAL_TIME_AMBIGUOUS`,
`LOCAL_TIME_OUTSIDE_HORIZON` y `POINT_OUTSIDE_AREA`.

`checked_rules` enumera estructura, relaciones, coherencia del snapshot,
horizonte, horarios locales y sus cambios de hora, rangos y precisión del
esquema, y área opcional cuando se indicó. `deferred_rules` identifica
publicación atómica y restricciones de revisión, asignación y reservas, y
factibilidad de rutas y flota. `VALID` significa que pasó esta validación, no
que un paquete se haya publicado ni que exista una solución de rutas. Un
paquete válido pero vacío puede recibir `VALID`; decidir si se publica queda
para 2.3c.

## Persistencia y recuperación

La migración `7a69c4d10e32` depende de `8db12e7c5f09`. Añade un contexto
inmutable y su SHA-256, un trabajo mutable con token de propietario,
`lease_until`, heartbeat e intentos, y un informe inmutable con huellas de
paquete, contexto e informe. Las incidencias van a `validation_issues` de 2.2.
Una petición crea contexto y trabajo y registra `RECEIVED → VALIDATING` en una
transacción. Los trabajadores reclaman mediante `FOR UPDATE SKIP LOCKED`. Una
concesión caducada se puede recuperar sin repetir esa transición. El token
impide que el trabajador antiguo confirme. Cada intento calcula el informe
completo sin incidencias parciales. Una transacción final vuelve a comprobar
token, vigencia, lote y expiración, y guarda informe, incidencias y transición
`VALID` o `INVALID`. Los fallos de infraestructura permiten reintento y, al
agotarse, llevan a `FAILED`. La unicidad por lote y ordinal de incidencias,
y el informe único por lote, impiden duplicados después de una interrupción.

La tarea de expiración omite lotes con concesión vigente. Si el plazo vence
antes del commit final, el trabajador no escribe `VALID` ni `INVALID`; la
expiración conserva sus metadatos e incidencias y permite limpiar originales.
La baja de la migración solo se permite cuando sus tres tablas están vacías.

## Traspaso verificable a 2.3c

Se eligió **reproducción verificada de los originales**: evita un segundo
artefacto normalizado privado y mantiene el límite de 20 MiB por archivo y
100 MiB por paquete. `replay_verified_for_publication` vuelve a leer por
`ObjectStorageGateway`, comprueba tamaño y SHA-256 de cada archivo y la huella
del paquete, interpreta el contexto y versiones inmutables, y reproduce el
informe con el mismo validador. Solo entrega los bytes acotados y contexto si
la huella del informe coincide. 2.3c deberá bloquear el lote y volver a
comprobar `VALID` y no expirado en su propia transacción de publicación antes
de insertar la revisión. No se publicará con otra versión del validador sin
una nueva carga y validación.

## Configuración y ejecución

| Variable | Predeterminado | Uso |
|---|---:|---|
| `ROUTEOPS_IMPORT_VALIDATION_LEASE_SECONDS` | 120 | Duración renovable de la concesión |
| `ROUTEOPS_IMPORT_VALIDATION_MAX_ATTEMPTS` | 3 | Intentos antes de `FAILED` |
| `ROUTEOPS_IMPORT_VALIDATION_POLL_SECONDS` | 5 | Espera del trabajador sin trabajo |

Compose ejecuta `python -m routeops.infrastructure.persistence.validation_worker
--loop` con acceso al mismo volumen privado de originales. Sin `--loop` procesa
como máximo un trabajo y sale. No añade Redis ni publica un puerto para el
trabajador. El CLI de 2.1 mantiene su validación sin contexto y su contrato de
salida original.
