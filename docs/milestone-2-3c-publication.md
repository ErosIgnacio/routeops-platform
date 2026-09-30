# Entrega 2.3c: publicación atómica de importaciones

Esta fase convierte un lote `VALID` en una revisión inmutable. La API sigue
disponible solo en `127.0.0.1`; no hay interfaz de importación ni optimización
de revisiones importadas.

## API

| Método | Ruta | Resultado |
|---|---|---|
| `POST` | `/api/v1/scenarios/{scenario_id}/imports/{batch_id}/publish` | `201` con revisión nueva; `200` con la misma revisión si ya estaba publicada |
| `GET` | `/api/v1/scenarios/{scenario_id}/revisions?after=0&limit=100` | Revisiones por número ascendente, `limit` de 1 a 200 y `next_after` |
| `GET` | `/api/v1/scenarios/{scenario_id}/revisions/{revision_no}` | Metadatos, contexto, conteos y fecha del snapshot importado |

Los errores previstos usan `code` estable: `BATCH_NOT_FOUND`,
`BATCH_NOT_VALID`, `BATCH_EXPIRED`, `SCENARIO_NOT_ACTIVE`,
`VALIDATED_SOURCE_UNAVAILABLE`, `VALIDATED_SOURCE_MISMATCH`,
`PUBLISH_PACKAGE_EMPTY`, `PUBLISH_INVENTORY_EMPTY`,
`PUBLISH_NORMALIZED_LIMIT`, `PUBLISH_TEMPORARY_STORAGE_FAILED`,
`PUBLICATION_INFRASTRUCTURE_FAILED`, `REVISION_NOT_FOUND` y
`PAGINATION_INVALID`. No se exponen rutas de
almacenamiento ni originales.

## Reproducción y transacción

El publicador usa la reproducción verificada de 2.3b. Comprueba tamaños y
SHA-256 de cada original, huella del paquete, contexto, versiones y huella
del informe. El mismo pase del validador emite las filas normalizadas a cinco
archivos temporales privados y acotados en memoria. Tras verificar el informe,
la inserción lee exclusivamente estas filas; no vuelve a abrir originales ni
a aplicar otras reglas de parseo. Los temporales se cierran y eliminan incluso
si la publicación falla. Su volumen agregado se limita a ocho veces el máximo
configurado para el paquete. Cada fila persiste su archivo de procedencia, número
de fila y SHA-256 de su representación normalizada.

Una transacción bloquea primero `import_batches` y luego `scenarios`, de
acuerdo con el orden del mantenimiento. Comprueba vigencia, estado `VALID`,
ausencia de errores y huellas. El bloqueo del escenario serializa números de
revisión de lotes distintos. Inserta revisión, centros, pedidos, líneas,
vehículos, snapshot e inventario en lotes de 500 filas con restricciones y
triggers activos. Registra `VALID → PUBLISHED` y su evento antes del commit.
Un error revierte la revisión, datos, estado y evento. Un reintento de un lote
ya publicado devuelve su revisión sin leer los originales.

Se rechaza un paquete completamente vacío. Se permiten `orders`,
`order_lines` o `vehicles` vacíos, así como las combinaciones coherentes que
admita el contrato, aunque una revisión sin pedidos o flota pueda no ser
ejecutable. Se exige al menos una fila de inventario para obtener un
`snapshot_at` de origen válido; un inventario vacío se rechaza sin fabricar
ese instante. El diagnóstico de ejecutabilidad anterior a optimizar es 2.5.
No se asignan pedidos ni se crean reservas en esta fase.

## Rendimiento pendiente

La inserción usa lotes dentro de una transacción, pero los triggers y las
restricciones permanecen habilitados. Antes de la interfaz de 2.3d hay que
medir con PostgreSQL/PostGIS real al menos 1 000, 10 000 y 100 000 filas
por dataset: tiempo total, tiempo por tabla, memoria máxima, tamaño de
temporales, WAL, espera por bloqueos y tiempo de rollback. Los límites
máximos del contrato no tienen rendimiento validado todavía.
