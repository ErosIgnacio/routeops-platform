# Hito 2 — aceptación integral (Entrega 2.6)

Estado: Hito 2 aceptado formalmente por el usuario. Este informe forma parte
del cierre versionado como `v0.2.0` sobre `main`. El cierre de 2.5 está en
`0755ac75ce42550e252d42ae46d53c09a6a7c140`; `v0.1.0` conserva el cierre
del Hito 1.

## Criterios y evidencia

| Criterio | Evidencia | Resultado |
| --- | --- | --- |
| CSV y XLSX: escenario, carga, validación, incidencias y publicación | `test_m26_http_acceptance.py`, `test_compose_stack.py`; navegador `/imports` | Conforme; `VALID` y `PUBLISHED` se distinguen. |
| Revisión publicada ejecutada con OSRM/VROOM reales | `test_http_import_to_planning_accept_or_cancel` para ambos formatos y smoke original | Conforme; rutas, origen, evidencia, snapshot y reservas consultables. |
| Aceptación, cancelación, recarga e historial | Pruebas HTTP y observación en `/planning` | Conforme; estados y reservas se recuperan. |
| Cuatro demos aisladas y `ORD-003` original | `test_http_four_independent_demos_and_original_regression` y pruebas de 2.4/2.5 | Conforme; `ORD-003` conserva `STOCK_NO_FULL_COVERAGE`. |
| Cargas y corridas repetidas; conflicto por contenido distinto | `test_http_invalid_report_and_concurrent_idempotency`, `test_http_concurrent_runs_and_accept_cancel_race` | Conforme: una identidad utilizable y HTTP 409 para conflicto. |
| Stock concurrente y aceptación frente a cancelación | Pruebas HTTP y transacciones reales de `test_persistence_migration.py` | Conforme: solo un ganador, sin doble confirmación o liberación. |
| Fallos, rollback, reintento, trabajador obsoleto y respuesta perdida | Integración PostgreSQL/PostGIS de 2.2–2.5; búsqueda por clave HTTP en 2.6 | Conforme; la concesión y token rechazan resultados tardíos. |
| Pedido completo, no ruteado y reserva externa | Integración `test_solver_unassigned_releases_all_order_lines`, `test_solver_unassigned_releases_only_routeops_stock`, guardas SQL | Conforme; no quedan reservas parciales ni se modifican las externas. |
| Nueva revisión y datos históricos | Integración de reconciliación y conservación, consulta de revisiones/runs | Conforme; los holds activos afectan disponibilidad entre revisiones. |
| Migración ascendente/descendente | Bases PostGIS descartables de `test_persistence_migration.py` | Conforme; no se destruyen datos locales. |
| Persistencia al reiniciar servicios | Consulta del mismo lote, revisión y corrida antes/después de `docker compose restart` | Conforme; mismos IDs, estado y 20 decisiones. |

Las pruebas de transacciones usan bases creadas y descartadas para cada caso.
Las pruebas HTTP crean escenarios con nombre `M26 acceptance` o `M26 HTTP
benchmark`. Las corridas creadas para medir se cancelan al acabar la muestra;
ninguna prueba borra corridas ni libera reservas de otros escenarios.

Verificación final: `pytest` completo en `backend`: **122 aprobadas**, una
advertencia no bloqueante de deprecación `starlette.testclient`/`httpx`;
integración PostgreSQL/PostGIS y migraciones: **54 aprobadas**; aceptación HTTP
nueva: **5 aprobadas**; frontend Vitest: **22 aprobadas**. Ruff lint y formato,
mypy estricto (53 módulos), TypeScript, build Vite y
`docker compose config --quiet` finalizaron correctamente. Alembic `current`
y `heads` coincidieron en `c951e2a7d430`. El smoke real tras reiniciar obtuvo
API `ready`, dos rutas con geometría, cuatro pedidos asignados, uno sin asignar
y `ORD-003` presente. El frontend respondió HTTP 200. El build Vite mostró
una advertencia de tamaño de bundle, sin error.

Para la prueba de reinicio se conservaron el lote
`1885132d-31ea-42e0-8421-9f22fde038c1`, la revisión
`aabdc236-fb31-47fb-9455-9a6c02f788ad` y la corrida
`de48446f-7525-4f0c-8a2e-109cbe253798`. Antes y después de
`docker compose restart`, el lote fue `PUBLISHED`, la corrida `CANCELED`, con
tres rutas históricas y 20 decisiones. `health/dependencies` volvió a `ready`
y el frontend respondió HTTP 200. El navegador recuperó el lote y la revisión
al recargar. Los volúmenes PostgreSQL y de originales privados no se borraron.

En el navegador se inspeccionaron CSV y XLSX publicados, un informe inválido
con `HEADER_MISSING` y `RELATION_MISSING`, el bloqueo de publicación y el
reintento de la revisión 1. Tras recargar `/imports` se recuperaron escenario,
lote e informe. En `/planning` se creó una demo de elección entre CDs, se
ejecutó y aceptó `e6138e93…`: la ruta `ORD-CHOICE → CD-A`, la reserva
`CONFIRMED` y el estado `ACCEPTED` aparecieron en el historial sin recarga
manual. Una segunda demo independiente de stock exclusivo produjo dos rutas;
al cancelar `a1f10cdf…`, ambas reservas pasaron de `HELD` a `RELEASED` y el
historial mostró `CANCELED` inmediatamente. La revisión histórica siguió
visible tras reiniciar servicios y recargar la página.

## Límites de planificación

Los topes configurables de 20 pedidos, 60 líneas, seis vehículos y 80 celdas
del producto `CD × pedido` se verifican antes de reservar. Las **80 celdas**
corresponden a la matriz de duración solicitada a OSRM para escoger origen; no
representan el trabajo total de VROOM. Para limitar la entrada de ruteo se
añade `ROUTEOPS_PLANNING_MAX_SOLVER_MATRIX_CELLS=1024`: antes de reservar se
comprueba `(pedidos + 2 × vehículos)^2 ≤ 1024`. El cálculo cuenta salida y
retorno de cada vehículo aunque compartan coordenadas. Es una cota
conservadora de ubicaciones potenciales, no una medición exacta de llamadas
internas de VROOM/OSRM ni un límite de filas de importación. Para la muestra
20/60/4/6 se obtienen 80 celdas de asignación y 32 entradas de coordenadas
potenciales para VROOM (20 trabajos y salida/retorno de seis vehículos), con
24 coordenadas distintas en los datos sintéticos (20 pedidos y cuatro CD).
Las consultas internas de VROOM a OSRM pueden deduplicar puntos y usan
matrices distintas de la llamada CD–pedido de RouteOps; no se instrumentó su
dimensión efectiva. Las 1024 celdas son una cota de admisión, no un tamaño
observado de esas matrices. Un exceso devuelve
`SOLVER_WORKLOAD_LIMIT` (HTTP 413) antes de crear reservas.
La activación de inventario también recorre el snapshot: se añade
`ROUTEOPS_PLANNING_MAX_INVENTORY_POSITIONS=10000`, con un conteo SQL previo a
crear la corrida. La muestra de 10.000 posiciones tardó 13,225 y 23,993 s
con dos clientes; superar ese tamaño requiere una revisión operativa más
pequeña o una mejora de activación medida. La publicación de un paquete mayor
sigue permitida, pero no se presenta como ejecutable con estos límites.
Se comprobó por HTTP que la revisión publicada con 240.000 posiciones devuelve
HTTP 413 al solicitar una corrida y conserva cero corridas; no llega a
reservar. El conteo de posiciones, pedidos, líneas y vehículos se hace en SQL
antes de materializar los datos del solver.

## Medición local reproducible

Comando desde `backend` con `PYTHONPATH=src`, variando argumentos:

```powershell
python scripts/benchmark_http_flow.py --format csv --inventory-rows 1000 --parallel 2 --planning
python scripts/benchmark_http_flow.py --format xlsx --inventory-rows 10000 --parallel 2
python scripts/benchmark_http_flow.py --format csv --inventory-rows 12 --parallel 2 --planning-workload
python scripts/benchmark_http_flow.py --format csv --inventory-rows 240000
python scripts/benchmark_http_flow.py --format xlsx --inventory-rows 160000
python scripts/benchmark_planning.py --orders 20 --centers 4 --vehicles 6 --lines-per-order 3
```

Entorno observado: Windows 11, 16 CPU lógicas, Docker Desktop con 15,44 GiB
disponibles. Cinco CSV contienen una fila en pedidos, líneas, CD y vehículos
salvo la muestra de planificación 20/60/4/6. Los tamaños corresponden a los
bytes realmente enviados: CSV de 1.000 filas de inventario, 50.739 B; CSV de
10.000, 500.739 B; XLSX de 1.000, 26.218 B; XLSX de 10.000, 224.726 B;
CSV de 240.000, 12.000.739 B; XLSX de 160.000, 3.555.970 B. La muestra
20/60/4/6 usa cinco CSV por 5.309 B en total y 12 filas de inventario.

| Muestra | Clientes | Carga HTTP (s) | Validación hasta estado final (s) | Publicación con reproducción (s) | Corrida hasta `READY` (s) | Errores |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CSV 10 | 1 | 0,042 | 5,017 | 0,027 | 2,522 | 0 |
| CSV 1.000 | 2 | 0,062 / 0,062 | 2,341 / 2,340 | 0,254 / 0,254 | 3,384 / 4,420 | 0 |
| CSV 10.000 | 2 | 0,067 / 0,066 | 1,291 / 1,290 | 2,088 / 2,099 | 13,225 / 23,993 | 0 |
| XLSX 1.000 | 2 | 0,037 / 0,027 | 2,768 / 2,778 | 0,363 / 0,362 | 1,499 / 2,745 | 0 |
| XLSX 10.000 | 2 | 0,031 / 0,020 | 1,918 / 2,636 | 3,214 / 2,477 | — | 0 |
| CSV 240.000, repetición 1 | 1 | 0,091 | 3,354 | 27,160 | — | 0 |
| CSV 240.000, repetición 2 | 1 | 0,091 | 6,481 | 27,041 | — | 0 |
| XLSX 160.000, repetición 1 | 1 | 0,042 | 11,479 | 25,738 | — | 0 |
| XLSX 160.000, repetición 2 | 1 | 0,041 | 10,406 | 25,381 | — | 0 |
| Plan 20/60/4/6, repetición 1 | 2 | 0,051 / 0,075 | 0,444 / 0,444 | 0,053 / 0,052 | 0,877 / 0,669 | 0 |
| Plan 20/60/4/6, repetición 2 | 2 | 0,047 / 0,047 | 2,760 / 2,761 | 0,053 / 0,058 | 1,089 / 1,296 | 0 |
| Plan 20/60/4/6, repetición 3 (límites finales) | 2 | 0,052 / 0,052 | 1,075 / 0,868 | 0,044 / 0,036 | 0,868 / 0,873 | 0 |

La latencia de validación y planificación incluye espera por polling y
disponibilidad de trabajadores; no es tiempo de CPU. El cliente se midió con
`perf_counter`. El RSS del cliente Windows se obtuvo con
`GetProcessMemoryInfo` cada 50 ms; el máximo en las muestras fue 90,7 MiB.
`docker stats --no-stream` muestreó cada ~0,5 s los contenedores. Los máximos
observados fueron aproximadamente 322 MiB backend, 263 MiB validador,
100 MiB trabajador de planificación, 274 MiB PostgreSQL, 61 MiB VROOM y
28 MiB OSRM. Es memoria total del contenedor, incluida caché, no consumo
incremental de una operación; picos entre muestras pueden escapar. La muestra
directa de planificación registró por separado 1.994.848 B de pico de
asignaciones Python con `tracemalloc`, matriz CD–pedido de 80 celdas, OSRM
128 ms, VROOM 47 ms y 20 tareas asignadas. `tracemalloc` no representa RSS de
Python ni memoria de contenedores.

Objetivos **locales de aceptación**, definidos retrospectivamente a partir de
estas muestras para este hardware: con dos clientes y 20/60/4/6, cada corrida
debe llegar a `READY` en 10 s y sin errores; con dos clientes y 1.000 filas,
cada paquete CSV/XLSX debe llegar a revisión publicada en 10 s. Los umbrales
dejan margen frente a los máximos observados (1,296 s por corrida y menos de
3,2 s para validar y publicar 1.000 filas), incluido el polling de los
trabajadores. Todas las muestras correspondientes los cumplen. Son umbrales
de revisión, no objetivos preinscritos ni SLO de producción. Los casos 10.000
y cercanos a límites se registran como
observación; no se extrapola capacidad máxima, SLO de producción ni tiempo de
planificación para 160.000–240.000 filas. Los triggers y restricciones SQL
permanecieron activos.

## Advertencias y decisiones posteriores

- El bundle Vite supera 500 kB minificados; el build completa y se puede
  dividir el código de interfaz en una entrega posterior.
- La política de ejecución de PowerShell de este equipo impidió invocar
  `scripts/smoke.ps1` directamente. Se ejecutaron sus comprobaciones HTTP
  equivalentes y el test de smoke integrado contra OSRM/VROOM reales.
- El polling añade variación a la latencia percibida. Un objetivo más estricto
  exige medir bajo hardware y concurrencia representativos del despliegue.
- La comprobación de habilidades, capacidad e inventario no demuestra que
  exista una ruta factible. VROOM permanece tras `SolverGateway`.
- La política `alternatives-v2` es determinista y resuelve la demo de pedido
  restringido; no tiene una prueba de optimalidad global.
- Autenticación, acceso externo, comparación global de estrategias y el
  siguiente hito están fuera de esta aceptación. Compose sigue limitado a
  `127.0.0.1`.

No se identificaron defectos bloqueantes en la aceptación integral. El cierre
publicado del Hito 2 queda identificado por `v0.2.0`.
