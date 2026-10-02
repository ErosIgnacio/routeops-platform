# Entrega 3.1b — KPIs del plan y tiempos de procesamiento

Estado: **aceptada y publicada el 2026-10-02**. Rama común:
`feat/m3-1-operation-analytics`. Commit `f21effb531a92fe556708989e93a79bde4209588`,
autor/committer Eros Moreno <erosignacio.m@gmail.com>, mensaje
`feat: add plan metrics and durable processing measurements`.
Se revisaron los 25 archivos y pasó `git diff --cached --check`; push de la rama,
fast-forward y push de main verificados directamente en origin. Etiquetas intactas.
La [continuación 3.1c](milestone-3-1c-diagnostics.md) incluye la validación integral
conjunta; permanece sin commit ni push. No se inició 3.2 ni 3.3.
Los resultados y el bloque Git de la revisión previa se conservan como historial.

## Cierre confirmado de 3.1a

Aceptación del usuario y publicación el 2026-10-02. Commit
`87f765079c1b03e5e76dfced795a04a32347920b`, autor
Eros Moreno <erosignacio.m@gmail.com>, mensaje
`feat: add operation contracts constraints and cost foundation`.
Los 38 archivos se revisaron y prepararon; `git diff --cached --check` pasó.
`origin/main` seguía en `0241a5168d9f7c02a152cf3614bf502659ea33b3`;
la integración fue fast-forward. `main` y la rama remota se verificaron
directamente con `git ls-remote` en el commit de cierre. Rama conservada.
Las etiquetas v0.1.0, v0.2.0 y v0.2.1 permanecieron intactas; ninguna nueva.
Las pruebas aprobadas de [3.1a](milestone-3-1a-contracts-costs.md) se conservan
como antecedente, sin contabilizarlas como nuevas ejecuciones de 3.1b.

## Implementación y contrato de consulta

`GET /api/v1/runs/{run_id}/metrics` consulta la demo original y las corridas
de revisiones importadas. Respuesta:

- `plan`: catálogo `plan-metrics-v1`, rutas, balance, costo operativo y objetivo
  del solver. `null` si todavía no existe un resultado, incluso en una corrida
  fallida. Ausencia de resultado nunca se presenta como un plan de cero rutas.
- `processing`: catálogo `processing-v1`, cola, intentos, fases, intervalos
  de recuperación y total transcurrido.
- `current_status` y `current_reservations`: situación actual separada del plan.
- `provenance`: identificador de revisión y hashes SHA-256 de entrada, resultado
  y rutas; fuente de entrada, cantidad de registros de tiempos y derivación
  histórica. No se exponen tokens de propietario ni rutas físicas.

Cada KPI incluye `value`, `unit`, `denominator`, `calculation_version`,
`provenance` y `unavailable_reason`. Conteos y medidas enteras permanecen
enteros; cocientes, estadísticas y dinero se serializan como cadenas Decimal.
El contexto decimal de 60 cifras evita convertir tarifas a flotantes. La
presentación y redondeo de estadísticas para UI/exportación corresponden a 3.3.

El cálculo reutilizable vive en `application/plan_metrics.py` y
`application/processing_metrics.py`. La consulta de persistencia extrae hechos
en una transacción de lectura `REPEATABLE READ`, para no mezclar una corrida
antes/después de su finalización o cancelación. El costo reutiliza exactamente
`estimate_operating_cost` de 3.1a mediante `OperatingCostQuery.from_run`;
no se repite su fórmula en la API ni en el frontend. Estos servicios son la
base para comparaciones y exportaciones posteriores.

Los endpoints y `kpis` históricos del tablero mantienen su forma y significado,
incluido `estimated_cost` como proxy histórico del objetivo del solver. Esta
entrega agrega un catálogo explícito; no reemplaza silenciosamente esas cifras.
No se agregó una pantalla analítica ni exportación.

## Catálogo y fórmulas

| KPI | Fórmula / unidad | Denominador y procedencia |
|---|---|---|
| Pedidos válidos de entrada | Número de IDs de pedido de la revisión publicada; nunca líneas | Revisión inmutable o snapshot persistido de entrada de la demo |
| Pedidos asignados a CD | Decisiones con CD seleccionado, aunque luego no tengan ruta | Decisiones inmutables de asignación; en demo nueva, entrada completa menos exclusiones ALLOCATION persistidas |
| Pedidos ruteados | Número de IDs únicos en pasos DELIVERY | Hechos de rutas persistidas; coordenadas coincidentes no fusionan pedidos |
| Pedidos no ruteados | Entrada válida − ruteados | Mismos IDs de entrada y resultado |
| Cobertura | Ruteados / entrada válida, razón 0..1 | Entrada válida; `null` con cero/ausencia |
| Distancia | Suma de metros por ruta; también individual | Totales persistidos reconciliados |
| km por pedido ruteado | Metros / 1,000 / pedidos ruteados; global y por ruta | Pedidos ruteados; `null` sin pedidos |
| Conducción, servicio, espera | Suma de segundos; global y por ruta | Cada concepto permanece separado |
| Duración operativa | Conducción + espera + servicio, segundos | Total de cada ruta y suma global; no tiempo del trabajador |
| Vehículos utilizados | Vehículos con ruta que contiene entregas | No incluye vehículos de flota sin ruta |
| Costo operativo | Fijo de vehículos usados + tarifa duty × segundos / 3,600 + tarifa km × metros / 1,000 | Moneda, tarifas, componentes, versión y hashes de 3.1a; total y por ruta |
| Costo por pedido ruteado | Costo operativo / pedidos ruteados, global y por ruta | `null` sin pedidos; no redondeo intermedio del cociente |
| Objetivo VROOM | Entero persistido y escala monetaria | Separado del costo completo; fijo + conducción + distancia del mapeo vigente; espera y servicio quedan fuera |
| Cumplimiento de ventanas | Inicio de servicio dentro de intervalo inclusivo / pedidos ruteados | Ventanas de entrada inmutable y `service_start_at`, global y por ruta |
| Balance de duración | Mínimo, máximo, media y desviación estándar poblacional de duty de vehículos usados | Media: suma/n; desviación: √(Σ(duty−media)²/n), segundos |
| CV de duración | Desviación poblacional / media | `null` con menos de dos vehículos usados o media cero, conforme al PRD |

Los ceros comprobables se conservan: un resultado sin rutas tiene cero metros,
segundos, vehículos y costo fijo utilizado. Su costo por pedido, cumplimiento
de ventanas y balance sin vehículos no tienen denominador y son `null`.
Sin datos históricos suficientes, el KPI afectado también es `null`, con motivo.
Identidades duplicadas/ajenas y discrepancias entre filas de rutas y resultado
producen un error controlado; no se ocultan deduplicándolas.

### Utilización independiente por dimensión

Se calculan por ruta unidades, peso en gramos y volumen en cm³, sin mezclarlos.
Máximo = carga máxima a bordo / capacidad. Promedio temporal =
Σ(carga a bordo del intervalo × segundos del intervalo) / (capacidad × duty).
Incluye conducción, espera y servicio, y el retorno al CD. La carga de un pedido
permanece a bordo hasta finalizar su servicio; `load_after` de VROOM es la carga
posterior a esa entrega y se aplica desde el siguiente intervalo. El inicio
contiene la carga inicial. No se promedian paradas ni porcentajes entre rutas.

Ejemplo comprobado: capacidad 10 unidades, carga inicial 8. Primer intervalo:
600 s de conducción + 300 s de espera + 300 s de servicio con carga 8. Segundo:
600 + 0 + 300 s con carga 4. Regreso: 300 s con carga 0. Duty = 2,400 s;
numerador = 8×1,200 + 4×900 = 13,200 unidades·s. Máximo = 0.8;
promedio = 13,200/(10×2,400) = **0.55**. Con capacidad 1,000 g y cargas
500/100/0, máximo = 0.5 y promedio = 0.2875; con 2,000 cm³ y cargas
400/200/0, máximo = 0.2 y promedio = 0.1375. Denominador nulo/cero → `null`.

Ejemplo de balance: dos rutas de 2,400 y 1,200 s dan media 1,800 s,
desviación poblacional 600 s y CV = 1/3. Una sola ruta tiene desviación 0,
pero CV `null`. Cinco pedidos de entrada, cuatro asignados y ruteados:
cobertura 0.8; no se cuentan sus líneas como pedidos adicionales.

## Tiempos, recuperación e inmutabilidad

Migración aditiva `f6b2d8a4c190`, dependiente de `e3a1b7c9d240`:
tabla `planning_timing_events`, índice por corrida/intento y trigger propio.
No hay backfill ni UPDATE de resultados, revisiones, snapshots o evidencia.
Cada límite de intento/fase es un registro append-only. Una restricción única
por corrida, intento, tipo y fase impide duplicar límites; se exigen intento
y fase iniciados antes de finalizarlos. Duraciones monotónicas en nanosegundos
no negativos; timestamps UTC persistidos y versión explícita.

La reclamación registra el intento en la misma transacción que la concesión.
El trigger y el repositorio comprueban estado RUNNING, token, número de intento
y concesión vigente. Recuperación/cancelación anexan una observación de
interrupción antes de reemplazar/retirar el token. Un trabajador anterior no
puede anexar duraciones ni finalizar resultados tardíos. Las mediciones finales
y el resultado READY o FAILED se confirman en la misma transacción de estado.
Una consulta no agrega registros ni cambia historia.

- **Cola inicial:** creación → primera reclamación. Demo síncrona nueva: 0,
  porque no tiene cola de trabajador; demo histórica sin evidencia: `null`.
- **Activo por intento:** `perf_counter_ns` del cuerpo del trabajador hasta
  su medición final. Incluye intentos fallidos conocidos. No se reconstruye
  con un reloj monotónico de otro proceso ni con el vencimiento de la concesión.
  Empieza después de confirmar la reclamación/inicio: adquisición de concesión
  y creación inicial no forman parte de ese intervalo monotónico. El total
  directo reconstruye ?nicamente el intervalo hasta el timestamp previo al commit.
- **Fases de revisiones:** VALIDATE_RUN, PREPARE_OSRM, ALLOCATE_RESERVE,
  PREPARE_SOLVER, SOLVER, RECONCILE, PERSIST_RESULT. Si ya existe la asignación,
  RECOVER_ALLOCATION reemplaza las fases que no se repiten. La demo síncrona
  agrupa asignación y consultas OSRM en ALLOCATION_OSRM_DEMO.
- **Persistencia:** la muestra exitosa llega hasta el flush de los hechos,
  antes del commit final y su acuse. Ese alcance se declara explícitamente;
  no se afirma medir ese acuse. Una llamada de persistencia fallida mide hasta
  su rollback cuando el proceso sobrevive. Una interrupción sin cierre deja
  duración de fase e intento `null`.
- **Entre intentos:** fin conocido del anterior → inicio del siguiente.
  Si solo se conoce cuándo se detectó la interrupción, la espera real es
  desconocida (`null`); la observación se muestra aparte. No se inventa cero.
- **Total directo:** creación → primer evento READY o terminal de procesamiento
  FAILED/CANCELED. ACCEPTED o cancelación posterior a READY no agregan espera
  del usuario. No es suma de fases, no incluye importación, polling ni tiempos
  operativos de ruta. Los timestamps reflejan el instante de transición
  persistido, no el acuse HTTP; dependen del reloj UTC del sistema.
  Un orden temporal negativo se devuelve como desconocido, nunca como duración
  negativa de procesamiento.

`active_all_attempts` es `null` si falta algún intento o su final.
`measured_active_subtotal` identifica por separado la suma parcial disponible.
`missing_attempt_numbers` muestra intentos históricos sin instrumentación.
Una cancelación antes del primer intento tiene activo comprobable 0.
No hay reintentos nuevos de fallos terminales: se mantiene la recuperación
existente de concesiones/interrupciones y su máximo configurado.

Los registros no admiten UPDATE/DELETE. El downgrade solo se permite con la
tabla de mediciones vacía, para impedir pérdida silenciosa de historia.
Elimina su tabla, índice, trigger y función; no cambia objetos previos ni PostGIS.
Las llamadas externas continúan fuera de bloqueos de inventario/escenario.

## Compatibilidad histórica

Las revisiones anteriores permiten recuperar pedidos, ventanas, capacidades y
tarifas de sus filas inmutables. Las demos nuevas guardan un snapshot mínimo
de IDs/ventanas y capacidades en su entrada, además de tarifas de 3.1a.
Una demo vieja sin ese snapshot no obtiene entrada, asignación, capacidades
ni ventanas de los fixtures actuales. Esas métricas quedan `null`; se conservan
los totales reconstruibles del resultado. Si faltan tarifas y hay rutas, el
costo queda `null`. Si no hay vehículos usados, el costo cero es comprobable
sin inventar tarifas. El endpoint de costo de 3.1a mantiene su comportamiento.

Las métricas describen **el plan calculado, no su ejecución física**. Aceptar
o cancelar cambia estado/reservas, no las cifras del plan. No se altera el
stock externo, no se repite una reserva desde una consulta, y no se recalcula
disponibilidad usando inventario actual. Deltas manuales corresponden a 3.2.

## Verificación proporcional ejecutada

| Comprobación | Resultado |
|---|---|
| Unitarias: `test_plan_metrics`, `test_processing_metrics`, `test_planning_service`, `test_operating_cost` | **30 aprobadas**, 0.75 s; después, subconjunto de 7 aprobado en 0.60 s con un caso nuevo de reloj: **31 casos distintos** |
| Integraciones seleccionadas de KPIs/lifecycle/API/stock en `test_persistence_migration` | **19 aprobadas**, 18.30 s, bases PostgreSQL/PostGIS descartables |
| Compatibilidad adicional: límites de vehículo, procedencia, replay 2.1, costo histórico, inmutabilidad y downgrade de corridas | **6 aprobadas**; una aserción antigua se corrigió para probar su revisión exacta |
| Persistencia fallida de la demo síncrona | **1 aprobada**, 2.63 s; fase FAILED medida, rollback atómico y resultado ausente |
| Smoke HTTP original `test_real_stack_routes_and_persists_demo` | **1 aprobada**, 0.58 s, VROOM/OSRM reales; métricas, costo y consulta repetida |
| Ruff | Aprobado sobre migrations/src/tests |
| mypy estricto | Aprobado, **62 módulos** |
| Compose | `docker compose config --quiet` aprobado |
| Migración de entorno local | `f6b2d8a4c190 (head)`; `alembic check`: sin operaciones nuevas |

Los conteos son selecciones, no una suite integral; ejecuciones intermedias
solapadas no se suman. Cobertura: paquetes/historia anteriores, cero rutas,
multilínea, coincidencia de coordenadas, capacidades distintas, espera/servicio,
ventana inclusiva por inicio de servicio, dinero/procedencia, población/CV,
rollback, trabajador obsoleto, cancelación en ejecución, recuperación con
intervalo desconocido, reserva externa intacta, aceptación/cancelación
concurrente y ausencia de locks durante llamadas externas.

El único fallo detectado fue una prueba histórica que contaba ocho triggers
desde una revisión antigua hasta todo `head`. Ahora mide explícitamente
`c951e2a7d430`; la prueba nueva comprueba de forma separada el trigger de tiempos
y la conservación de objetos/PostGIS. No se eliminaron ni omitieron pruebas.

### Muestra real del smoke

Corrida `98e5dc10-d2e1-402f-b6f6-aaaa8d1278f9`, estado PARTIAL, dos vehículos,
cinco pedidos de entrada, cuatro asignados/ruteados; ORD-003 mantiene
STOCK_NO_FULL_COVERAGE. Distancia 8,218 m; conducción 897 s; servicio 2,700 s;
espera 0 s; duty total 3,597 s. Costo operativo CLP 53,118.7711.
Duty individual 1,615/1,982 s; media 1,798.5 s; desviación 183.5 s;
CV ≈ 0.102029469. Es una muestra funcional, no un objetivo de rendimiento.

Intervalo reconstruido previo al commit 0.229736 s, activo medido 0.201148968 s.
Fases: asignación/OSRM 0.118922895 s; preparación 0.000187398 s;
solver 0.032997699 s; reconciliación 0.001015816 s; persistencia hasta flush
0.026793022 s. Su suma no reemplaza al total directo ni al activo del intento.
Estos datos se consultaron por API; no constituyen una revisión visual nueva.

## Evidencia conservada y validación diferida

Se conserva lo aprobado de 3.1a: contratos/límites reales, reportes/hashes de
importación y versiones antiguas, frontend 27 pruebas, TypeScript, build y
revisión visual de v0.2.1. No hay cambios de frontend ni del contrato consumido
por sus pantallas; no se repitieron sus suites ni las cuatro demos por rutina.
La consulta nueva es aditiva y se probó por HTTP/TestClient. El frontend sigue
disponible localmente; no se declara verificación visual durante esta entrega.

Pendientes del cierre integral de 3.1a–c: suite backend completa, integraciones
y migraciones completas, smoke/demos reales conjuntos y comprobaciones frontend
correspondientes. Diagnósticos causales y casos B2B/B2C: 3.1c. Comparación manual:
3.2. Pantallas analíticas y exportación: 3.3.

Limitaciones: relojes UTC de procesos/host deben estar sincronizados; commit
final/acuse HTTP no tienen duración monotónica propia; muertes del trabajador
dejan intervalos desconocidos; no se inventa telemetry histórica. El catálogo
no demuestra ejecución real, optimización global ni capacidad máxima del solver.
Se conserva la advertencia existente de Starlette/httpx en pruebas; no bloquea.

La comprobación final del contrato del objetivo (`unit`, versión y procedencia)
pasó en TestClient junto con su prueba de inmutabilidad; es una repetición
dirigida ya incluida en el conteo de integraciones. API lista (`200 ready`) y
frontend desde Windows en `http://127.0.0.1:5173/` (`200`). La petición al nombre
interno `frontend` recibió `403` de la protección de hosts de Vite; al usar el
host local autorizado respondió `200`. No se cambió esa protección.

## Archivos y estado Git de la revisión previa al cierre

Repositorio: `C:\Users\erosi\Desktop\routeops-platform`.
Rama activa `feat/m3-1-operation-analytics`, HEAD de cierre 3.1a
`87f765079c1b03e5e76dfced795a04a32347920b`. 3.1b: **25 archivos, 16
modificados y 9 nuevos**, sin preparar, commit ni push. Revisión de archivos
nuevos y `git diff --check` aprobadas; sin secretos ni artefactos incorporados.

```text
 M README.md
 M backend/src/routeops/api/main.py
 M backend/src/routeops/application/planning.py
 M backend/src/routeops/application/ports/gateways.py
 M backend/src/routeops/infrastructure/persistence/models.py
 M backend/src/routeops/infrastructure/persistence/operating_costs.py
 M backend/src/routeops/infrastructure/persistence/repository.py
 M backend/src/routeops/infrastructure/persistence/revision_runs.py
 M backend/tests/integration/test_compose_stack.py
 M backend/tests/integration/test_persistence_migration.py
 M backend/tests/unit/test_planning_service.py
 M docs/architecture.md
 M docs/data-model.md
 M docs/optimization-contract.md
 M docs/product-requirements.md
 M docs/roadmap.md
?? backend/migrations/versions/f6b2d8a4c190_add_processing_measurements.py
?? backend/src/routeops/application/plan_metrics.py
?? backend/src/routeops/application/processing_metrics.py
?? backend/src/routeops/application/processing_times.py
?? backend/src/routeops/infrastructure/persistence/plan_metrics.py
?? backend/src/routeops/infrastructure/persistence/processing_times.py
?? backend/tests/unit/test_plan_metrics.py
?? backend/tests/unit/test_processing_metrics.py
?? docs/milestone-3-1b-metrics.md
```

## Comprobación final del límite de confirmación (3.1b)

`total_elapsed` conserva su valor histórico, derivado de timestamps UTC; se
clasifica explícitamente `PARTIAL`, con `derivation=RECONSTRUCTED` y
`endpoint=transition_timestamp_before_final_commit`. El instante final se toma
dentro de la transacción, después del flush y antes del COMMIT. Que la fila sea
visible durablemente al consultar no convierte ese timestamp en un acuse del
commit. Ni el envío, espera o acuse del COMMIT ni el acuse HTTP están medidos.
`durable_total_elapsed` es `null`, `UNKNOWN`, motivo `COMMIT_ACK_NOT_RECORDED`.
No se agrega una duración estimada ni se reescribe la historia.

La cola y esperas entre intentos son `RECONSTRUCTED` con timestamps; duraciones
monotónicas son `MEASURED` dentro del alcance declarado, intervalos faltantes
`UNKNOWN` y el subtotal de intentos incompletos `PARTIAL`. Un total directo no
es una suma de fases. Los KPIs del plan siguen siendo reconstrucciones de
hechos inmutables, no mediciones de ejecución física. Esta precisión del
contrato se comprobó con seis pruebas de tiempos, Ruff y mypy antes de publicar.
La validación integral posterior está registrada en el informe 3.1c.
