# Entrega 3.2 — comparación manual y de políticas

Estado: **aceptada para cierre tras comprobación temporal dirigida**. 2026-10-02.
Repositorio `C:\Users\erosi\Desktop\routeops-platform`, rama única
`feat/m3-2-plan-comparison`. No se inició 3.3 ni se crearon etiquetas.

## Cierre publicado de 3.1

Commit `0f95fcf507ea219345836cbaeca26e86c1787bd4`, autor/committer
Eros Moreno <erosignacio.m@gmail.com>, mensaje
`feat: add auditable planning diagnostics and operation cases`.
Exactamente 29 archivos revisados y preparados, incluidos nueve nuevos;
`git diff --cached --check` aprobado, sin secretos ni artefactos.
Tras fetch, origin/main permanecía en
`f21effb531a92fe556708989e93a79bde4209588`.
Push de `feat/m3-1-operation-analytics`, fast-forward y push de main aprobados;
`git ls-remote` confirmó ambas referencias en 0f95fcf…; la rama se conserva.

Las dos correcciones de downgrade comparan el head anterior/posterior al
rollback completo y prueban por separado guardias de historia de diagnósticos
y tiempos. No se retiraron comprobaciones de inmutabilidad, filas o PostGIS.
El [informe 3.1](milestone-3-1c-diagnostics.md) diferencia 275 aprobadas + dos
expectativas corregidas de su ejecución integral inicial, las cinco
comprobaciones posteriores y 278 casos distintos aprobados, sin sumar repeticiones.
Conserva restricciones, costos, KPIs, diagnósticos y cinco casos B2B/B2C.

El total temporal reconstruido de 3.1b termina antes del COMMIT final y sigue
PARTIAL; el acuse durable es UNKNOWN/null. Tampoco los timestamps de eventos
de comparación miden un acuse COMMIT/HTTP. Un evento RESULT_COMMITTED es el
registro transaccional del resultado, visible al confirmar, no una medición
de ese acuse. No se rellenaron intervalos desconocidos ni historias.

Etiquetas preservadas, verificadas en origin:

| Etiqueta | Commit |
|---|---|
| v0.1.0 | 449e671822622919594fb5fa73e633cf32cf64c2 |
| v0.2.0 | 4a99f99708f9b4a31bf9d699e4b2ec91238c7021 |
| v0.2.1 | 0241a5168d9f7c02a152cf3614bf502659ea33b3 |

## Contrato y estados

`plan-comparison-v1` evalúa tres planes: `manual`, `greedy-v1` y
`alternatives-v2`. No activa reglas por etiqueta B2B/B2C. No crea planning_runs,
allocation_attempts, reservas ni movimientos de inventario. No se acepta o
cancela una simulación como si fuera una corrida operacional.

| API | Entrada / salida |
|---|---|
| POST `/api/v1/scenarios/{scenario_id}/revisions/{revision_no}/comparisons` | Idempotency-Key obligatorio; manual_routes y solution_quality FAST/BALANCED/THOROUGH; 202 nuevo / 200 existente |
| GET `/api/v1/comparisons/{comparison_id}` | Estado, intentos, contexto/hash, entrada, resultado/hash e historial; no token interno |
| GET `/api/v1/scenarios/{scenario_id}/comparisons` | offset>=0, limit1..100; items, total y next_offset |

Entrada manual:

```json
{
  "manual_routes": [
    {"vehicle_id": "TRUCK", "center_id": "CD-B2B", "order_ids": ["B2B-OK", "B2B-SECOND"]}
  ],
  "solution_quality": "BALANCED"
}
```

IDs de negocio pertenecientes a la revisión. El orden de rutas y de pedidos
es significativo, se conserva y participa en el hash. Campos desconocidos
(incluido historical_run_id y tiempos declarados) se rechazan; esta entrega
no admite horas declaradas por el usuario. Los IDs deben ser strings de 1..100
caracteres. El esquema admite hasta seis rutas y veinte referencias; además
el servicio aplica los límites configurados y los de la revisión antes de
materializar el contexto. Una lista vacía representa un baseline sin pedidos.

Estados: QUEUED → RUNNING → READY, o RUNNING → QUEUED por fallo recuperable;
RUNNING → RUNNING solo al recuperar concesión vencida. FAILED es terminal
por agotamiento de reintentos o incompatibilidad/integridad del contexto o
respuesta inválida. Un límite de intentos reducido puede terminar QUEUED → FAILED.
READY y FAILED no se reabren. READY significa evaluación disponible, **no** que
el plan manual sea viable: cada alternativa tiene feasible y cobertura separados.

Clave única por escenario; su hash incluye revisión, rutas manuales y calidad.
La misma clave/contenido devuelve la comparación y contexto originales, incluso
si cambia posteriormente la disponibilidad. Contenido distinto produce
COMPARISON_IDEMPOTENCY_CONFLICT/409. No se recaptura stock al recuperar respuestas.

Otros errores: COMPARISON_KEY_REQUIRED/INVALID422, COMPARISON_NOT_FOUND404,
PAGINATION_INVALID422, COMPARISON_WORKLOAD_LIMIT o SOLVER_WORKLOAD_LIMIT413,
COMPARISON_REQUIRES_CURRENT_REVISION409, INVENTORY_RECONCILIATION_CONFLICT409,
COMPARISON_FACTS_INVALID409 y COMPARISON_CONTEXT_VERSION_UNSUPPORTED.
Las causas operacionales conservan códigos seguros, no texto de excepciones.

## Contexto congelado y consistencia

La creación bloquea brevemente el escenario con el mismo orden que publicación
y operaciones de inventario. Captura revisión publicada **actual**, preparación
normalizada y líneas del snapshot importado; lee en una consulta las posiciones
operacionales del escenario. No llama a OSRM/VROOM ni activa inventario bajo
ese bloqueo. Todas las operaciones de negocio que cambian las reservas usan
el mismo bloqueo de escenario. La lectura siguiente es de datos inmutables.

Para cada CD/SKU:

`available = on_hand - externally_reserved - safety_stock - routeops_reserved`

Los primeros tres conceptos proceden del snapshot de la revisión actual;
RouteOps reservado procede de la posición operacional al capturar, incluyendo
HELD y CONFIRMED. Una revisión nueva conserva esos contadores por CD/SKU:
se simula la misma reconciliación de activación aprobada en 2.4 **sin escribirla**.
Si el nuevo stock base no cubre las reservas vigentes, se rechaza el contexto.
Una revisión nueva no inventa stock libre ni convierte las reservas externas
en reservas RouteOps. SKUs sin posición importada no suministran cobertura.

Se conservan IDs/hashes de revisión, contexto de importación, paquete/informe,
contrato de archivos y validador, snapshot/línea/procedencia de fila y origen
de reserva operacional; timestamp de captura; horizonte, zona IANA, moneda,
órdenes/líneas/CD/flota, tarifas escaladas exactas y restricciones normalizadas.
Versiones: comparación/manual/KPIs/costo/diagnósticos/políticas, contrato de
solver, VROOM1.15.0/adaptador1.2.0, OSRM26.9.0/perfil car y checksum configurado.
Son versiones/dataset fijados por la configuración existente, no una nueva
atestación dinámica del binario/mapa remoto. Parámetros de calidad, timeout
y tolerancia de snap quedan congelados; no se amplían extracto ni límites.

Cada alternativa recibe la misma tupla inmutable de stock; genera solo un
mapa local de disponibilidad. Hash inicial idéntico registrado en las tres.
El trabajador verifica hashes/versiones y compara la preparación de la revisión
inmutable con la normalización guardada; nunca consulta stock actual para evaluar.
Una versión incompatible falla explícitamente. Una comparación ya terminada
se consulta sin recalcularla ni reinterpretarla.

No se admite construir una comparación desde una corrida histórica en esta
entrega. Se rechaza ese campo; no se sustituye stock histórico por el actual.
Sí se conservan/consultan comparaciones históricas ya congeladas, aunque después
se publique otra revisión.

## Evaluación manual reproducible

`fixed-sequence-earliest-v1` no busca, reordena ni repara una secuencia.
OSRM **route**, no trip, obtiene cada trayecto CD → pedidos en orden → CD,
geometría completa y snaps. Se controlan pares/segmentos ausentes, métricas
finitas no negativas, número de piernas y tolerancia de snap configurada.
Cada distancia/duración de pierna se redondea una sola vez a metros/segundos
enteros con round (empate al par); los totales suman esas piernas. OSRM tiene
su propio routing vial; el evaluador no incorpora un solver de pedidos.

1. Sale a la apertura efectiva del turno, intersectado con CD/horizonte.
2. Llegada = salida anterior + conducción de la pierna.
3. Espera = max(0, inicio de ventana − llegada).
4. Inicio de servicio = llegada + espera, dentro de la ventana inclusiva.
5. Salida = inicio de servicio + duración exacta del pedido.
6. El pedido permanece a bordo durante conducción, espera y servicio; se
   descuenta después. El retorno debe caber en el turno efectivo.

La carga inicial incluye todos los pedidos de la ruta. Se comprueban las tres
dimensiones individuales y skills, vehículo/CD, unicidad y pertenencia de IDs,
stock completo de todas las líneas y consumo compartido entre rutas, ventanas,
servicio, cierre efectivo y máximos de distancia/conducción/tareas. No se suman
capacidades de vehículos para cumplir un pedido. START/END no cuentan como tareas.

Catálogo manual: MANUAL_ORDER_UNKNOWN/DUPLICATE, MANUAL_VEHICLE_UNKNOWN/DUPLICATE,
MANUAL_CENTER_UNKNOWN, MANUAL_EMPTY_ROUTE, MANUAL_VEHICLE_CENTER_MISMATCH,
MANUAL_SKILLS, MANUAL_CAPACITY, MANUAL_STOCK_NO_FULL_COVERAGE, MANUAL_WINDOW,
MANUAL_EFFECTIVE_SHIFT, MANUAL_DISTANCE_LIMIT, MANUAL_DRIVING_LIMIT y MANUAL_TASK_LIMIT.
Son comprobaciones PROVEN de la entrada fija; no demuestran inviabilidad global.
MANUAL_ORDER_NOT_SERVED es advertencia de cobertura. No atiende pedidos omitidos
automáticamente. Una ruta con identidad inválida queda en la entrada y sus
incidencias, sin fabricar geometría/métricas; rutas conocidas inviables conservan
cronología y métricas **informativas**, feasible=false. Un fallo de dependencia
es un fallo de procesamiento separado, no una infracción inventada de stock.

Convención importante: el manual sale al inicio del turno; VROOM puede escoger
otra salida válida. Las diferencias incluyen esa libertad temporal, no solo
orden de paradas. No se descuentan esperas del manual para producir una ventaja.

### Comprobación temporal previa al cierre

La entrada evaluada `alternatives.manual.evaluated_input` incluye la política
EFFECTIVE_SHIFT_START y el instante concreto cuando el vehículo existe.
Cada ruta conserva departure_condition (policy, departure_at, scope); las
alternativas optimizadas indican SOLVER_CHOSEN_WITHIN_EFFECTIVE_SHIFT. La consulta
ofrece estos campos a interfaz/exportaciones. Son condiciones específicas del
plan, no una obligación común de salir a la misma hora. Los pasos/totales y KPIs
separan conducción, servicio, espera y duración operativa. Los pares de deltas
incluyen temporal_scope y una advertencia de atribución: diferencias observadas
de planes, no ahorro causado exclusivamente por secuencia. No cambia la salida
manual ni la semántica de los cálculos; son campos compatibles adicionales.
Resultados previos permanecen inmutables y carecen de este metadato adicional.

Comprobación dirigida: cronología con 3,500s de espera, 200s de conducción,
600s de servicio y 4,300s de duración; instantes/condiciones en entrada evaluada
y rutas, y alcance temporal en deltas. Cinco casos reales B2B/B2C comprobaron
también los campos optimizados. **6 aprobadas, 28 fuera de selección, 6.48s**;
Ruff y mypy estricto70 módulos aprobados. Un primer intento obtuvo 1 aprobada y
5 fallos de conexión porque faltaban ROUTEOPS_TEST_OSRM_URL/TEST_VROOM_URL en el
contenedor (defaults Windows 127.0.0.1); se repitió con osrm:5000/vroom:3000.
No se omitieron los fallos ni se cambió código para ocultarlos. La revisión
temporal añade metadatos; no altera cálculos ni reservas. Se conserva la
aceptación anterior y no se repitió integralmente por este ajuste aislado.

Las tres expectativas HTTP anteriores se corregían al contrato existente
items/next_offset: lista operacional vacía para una simulación; el listado de
comparaciones sí devuelve total. No se eliminó una condición funcional ni se
añadió un campo artificial a la API operacional. Ejecución inicial y repetición
se conservan separadas en el apartado de resultados. Limitaciones aceptadas:
sin optimalidad global, baselines históricos operacionales ni tiempos declarados.
No se ofrecen horarios declarados ni se imita una hora de salida optimizada.

## Políticas y métricas

Se reutilizan OperationalAllocationPolicy, FrozenTravelTimes, PreparedRevision,
conciliación, SolverGateway, diagnóstico central, estimate_operating_cost y
plan_metrics. Una matriz CD–pedido se comparte por ambas políticas; dos
problemas distintos reciben la misma revisión, inventario inicial, flota,
tarifas y calidad. VROOM se llama exclusivamente mediante SolverGateway.
Cada resultado debe reconciliar restricciones y versiones antes de persistirse.
Decisiones y diagnóstico guardan candidatos, stock/skills/capacidades, rankings,
OSRM y política; reservas analíticas no se crean ni compensan.

Reutilización del catálogo plan-metrics-v1: cobertura/unidades/peso/volumen,
tiempos, utilización, ventanas, vehículos, costo operativo Decimal y procedencia.
Una ruta manual no es un OptimizationResult del solver: carece de objetivo
VROOM. `solver_objective=null` para manual y solver omitido por cero tareas;
los objetivos de los planes optimizados se muestran separados, sin delta de
objetivo ni comparación con el costo operativo.

Pares: manual→greedy, manual→alternatives y greedy→alternatives.
Para distancia, duty, conducción, costo, vehículos, pedidos y cobertura:

`absolute = candidate - baseline`

`percentage = (candidate - baseline) / baseline × 100`

Valores Decimal en strings; unidad y denominador explícitos. Baseline cero o
valor desconocido → percentage=null; faltante → absolute=null. Una reducción
produce valor negativo. Cobertura usa razón 0..1; absolute es diferencia de
razones, no puntos porcentuales. No se mezclan monedas/versiones/contextos.

Alcance implementado: **conjunto completo de entrada**; no se recalculan rutas
filtrando a pedidos comunes. COMPARABLE se expresa mediante FULL_COVERAGE,
SAME_PARTIAL_COVERAGE, DIFFERENT_COVERAGE o INFEASIBLE_PLAN y flags de viabilidad,
misma población servida/cobertura completa. savings_claim_allowed solo es true
si ambos planes son factibles y sirven todo el mismo conjunto. No constituye
una afirmación automática de ahorro ni de ganador: winner=null siempre.

Ejemplo unitario, no medición real: dos piernas de 1,000m/100s, espera3,500s,
servicio600s → duty4,300s. Tarifas fijo10, hora5, km1 → costo17.9722.
Comparar 2,000m con 0m da −2,000/−100%, pero si el segundo sirve cero pedidos
no permite afirmar ahorro. Invertir ese baseline da porcentaje null.

## Persistencia y trabajo recuperable

Migración aditiva `b82d6c4a910f`, después de `a71c9e3d602b`, sin backfill:

| Tabla | Propósito / claves y garantías |
|---|---|
| plan_comparisons | PK UUID; FK escenario/revisión RESTRICT con comprobación de pertenencia; clave única escenario+client_key; contexto/entrada/hashes/UTC inmutables; índice revisión/creación |
| comparison_jobs | PK/FK comparison_id RESTRICT; estados, versión, intentos, concesión/token y fecha de reintento; CHECK de estado/contadores/par de concesión; índice de claim |
| comparison_events | PK UUID; FK job RESTRICT; secuencia única, transición/fecha/intento/código; append-only y cadena coherente |
| comparison_results | PK/FK job RESTRICT; documento completo, hashes contexto/contenido y propietario/intento interno; append-only y una fila por comparación |

Triggers propios rechazan mutación/eliminación de contexto, entrada, eventos,
resultados y jobs terminales. Guardias de transición/heartbeat y constraint
triggers diferidos obligan a concordar estado/versión/intento/fecha con el
último evento y a tener resultado exactamente cuando READY.
El INSERT de resultado verifica en DB token, intento, estado y concesión con
clock_timestamp, además de correspondencia del contexto. Un propietario vencido
o reemplazado no puede confirmar resultados aunque llame directamente a SQL.
El API verifica hashes al consultar; no revela el token de propietario.

Patrón de coordinación ya usado por las corridas: PostgreSQL SKIP LOCKED,
concesión con heartbeat, recuperación y máximos configurables; adaptado a tablas
analíticas. Nuevo servicio Compose comparison-worker, sin puerto publicado ni
Redis. Usa las variables ROUTEOPS_PLANNING_LEASE_SECONDS/MAX_ATTEMPTS/POLL_SECONDS,
límites actuales y configuraciones OSRM/VROOM. Backoff de fallo recuperable5s
en el servicio; no caduca ni libera reservas operacionales.

Toda llamada externa ocurre después de cerrar la lectura de contexto y antes
de abrir la transacción final. No mantiene bloqueos de inventario/escenario.
No se guardan alternativas parciales: resultado, transición y evento READY se
confirman en una transacción. Fallo/interrupción antes del commit → rollback y
reintento de todo el análisis desde el contexto original, sin duplicados. Tras
commit/respuesta perdida → POST con la misma clave o GET devuelve lo guardado.
El solver puede variar entre intentos con varias soluciones; solo se conserva
el resultado del propietario que confirma, junto con el historial de intentos.

Downgrade solo con plan_comparisons vacío. Si hay cualquier comparación incluso
QUEUED/FAILED, rechaza pérdida de historia. El permitido elimina únicamente
cuatro tablas/seis triggers asociados/tres funciones propias; preserva PostGIS
y objetos de 3.1. La revisión Alembic declara sus tablas explícitamente, sin
importar modelos ORM cambiantes.

## Reproducción local por API

Los casos existentes sirven para nuevos escenarios aislados. Ejemplo factible:

```powershell
$base = 'http://127.0.0.1:8000/api/v1'
$prepared = Invoke-RestMethod -Method Post -Uri "$base/operation-cases/b2b-feasible/prepare"
$key = [guid]::NewGuid().ToString('N')
$body = @{manual_routes=@(@{vehicle_id='TRUCK';center_id='CD-B2B';order_ids=@('B2B-OK','B2B-SECOND')});solution_quality='BALANCED'} | ConvertTo-Json -Depth 8
$path = "$base/scenarios/$($prepared.scenario_id)/revisions/1/comparisons"
$queued = Invoke-RestMethod -Method Post -Uri $path -Headers @{'Idempotency-Key'=$key} -ContentType 'application/json' -Body $body
Invoke-RestMethod -Uri "$base/comparisons/$($queued.comparison_id)" | ConvertTo-Json -Depth 12
```

Consultar hasta READY/FAILED; repetir POST con $key/$body recupera el mismo ID.
Para B2C, preparar b2c-task-pressure y VAN/CD-B2C con B2C-01..B2C-06 en ese orden.
Para stock compartido, preparar `/allocation-demos/shared_stock_restricted/prepare`;
manual inviable: VEH-CD-A/CD-A con ORD-FLEX y ORD-RESTRICTED. Manual factible:
VEH-CD-B/CD-B con ORD-FLEX y VEH-CD-A/CD-A con ORD-RESTRICTED.
Greedy asigna flexible al CD-A y excluye restringido; alternatives conserva CD-A
para restringido y usa CD-B para flexible. No se ejecutan corridas consecutivas
contra stock real para simular esa comparación.

Los archivos pueden generarse con el CLI de operation_cases e importarse por
CSV/XLSX existentes. No cambian encabezados, contratos de importación ni UI.

## Resultados y evidencia de pruebas

Evidencia **automatizada y HTTP/API**, sin revisión nueva de navegador.
Todos los contenedores y locks existentes; Python3.14.7, PostgreSQL18/PostGIS3.6,
OSRM26.9.0/VROOM1.15.0. URLs internas osrm:5000/vroom:3000/backend:8000;
Windows usa 127.0.0.1. Tests con PostgreSQL en bases descartables; no se modifican
registros históricos ni reservas locales anteriores.

| Comprobación | Resultado |
|---|---|
| Desarrollo dirigido | 30 aprobadas (18 unitarias +12 integraciones), luego 2 integraciones reforzadas aprobadas; incluidas en la selección final |
| Selección final proporcional | 223 ejecutadas, 220 aprobadas y 3 fallos de una expectativa nueva de test HTTP; 47.96s, sin skips |
| Corrección/repetición afectada | 3 HTTP aprobadas en 12.11s: el listado operacional existente usa items/next_offset, no total; se comprobó lista vacía sin modificar su API |
| Estado final | **223 casos distintos aprobados: 201 unitarios y 22 integraciones**, no suma de repeticiones |
| Static | Ruff aprobado src/tests/migrations; mypy estricto70 módulos |
| Alembic/Compose | current b82d6c4a910f(head), check sin nuevas operaciones y config --quiet aprobado |
| Manual | Identidades duplicadas/desconocidas/omitidas, skills, capacidades3D, stock compartido, CD, ventanas exactas/exceso, turno y máximos; secuencia y esperas conservadas |
| PostGIS | Idempotencia concurrente, conflicto, captura de HELD entre revisiones, stock externo intacto, fencing DB, rollback después de flush, reintentos/agotamiento y eventos terminales coherentes |
| Historia/migraciones afectadas | Preservación Hito1, downgrade vacío/lossy, guardias independientes de tiempo/diagnóstico y consulta histórica sin backfill |
| Real | Cinco casos B2B/B2C, caso compartido greedy/alternatives, CSV y XLSX por HTTP y smoke original real |

Selección ejecutada dentro de un contenedor de comprobación con las dependencias
dev fijadas, src/tests/migrations montados y URLs internas de Compose. La URL
de la base se recibe del entorno local, nunca se copia a documentación:

```sh
export ROUTEOPS_TEST_DATABASE_URL="$ROUTEOPS_DATABASE_URL"
export ROUTEOPS_INTEGRATION_BASE_URL=http://backend:8000
python -m pytest -q -p no:cacheprovider --junitxml=/tmp/m32-acceptance.xml \
  tests/unit \
  tests/integration/test_plan_comparisons.py \
  tests/integration/test_m32_http_comparisons.py \
  tests/integration/test_compose_stack.py::test_real_stack_routes_and_persists_demo \
  tests/integration/test_persistence_migration.py::test_upgrade_preserves_hito_1_and_empty_downgrade \
  tests/integration/test_persistence_migration.py::test_revisions_keys_links_and_downgrade_guard \
  tests/integration/test_persistence_migration.py::test_processing_measurements_are_immutable_and_refuse_lossy_downgrade \
  tests/integration/test_planning_diagnostics.py::test_diagnostic_migration_empty_downgrade_and_legacy_history \
  tests/integration/test_planning_diagnostics.py::test_timing_guard_still_blocks_loss_when_diagnostic_table_is_empty \
  --tb=short
python -m pytest -q -p no:cacheprovider \
  --junitxml=/tmp/m32-http-final.xml tests/integration/test_m32_http_comparisons.py
ruff check src tests migrations
mypy src
alembic current
alembic check
```

Los dos JUnit se inspeccionaron: selección inicial tests=223/failures=3/errors=0/
skipped=0; repetición afectada tests=3/failures=0/errors=0/skipped=0. El contenedor
descartable de comprobación se retira al finalizar; no forma parte de la aplicación.

La primera aserción monetaria de desarrollo tenía una expectativa aritmética
errónea; se corrigió a fijo10 + hora5 ×4,300/3,600 + km1 ×2 =17.9722 y pasó.
Una selección anterior perdió su proceso/salida final al cambiar la sesión de
herramientas: no se declaró aprobada; se repitió la selección con JUnit en el
contenedor propio. Las tres expectativas HTTP incorrectas se registraron como
fallos antes de corregirse y repetir solamente esos tests. No se omitieron tests.
La guardia anterior de tiempos ahora compara el head capturado antes/después
del rollback, conservando sus aserciones de filas/inmutabilidad; evita hardcodear
el head 3.1 cuando la nueva migración vacía también se revierte.

No se repitieron la suite backend integral ajena, las cuatro demos anteriores,
frontend/TypeScript/build ni revisión visual completa: el frontend y sus
contratos existentes no cambiaron. Se conserva la aceptación conjunta de 3.1;
se comprobó el smoke original porque el adaptador OSRM comparte el módulo nuevo.
Las bases descartables se limpiaron; los escenarios HTTP identificables se
conservan, incluidas las ejecuciones con test final fallido/salida perdida.

### Muestras HTTP finales persistidas

| Caso | Escenario | Comparación |
|---|---|---|
| B2B factible XLSX | 3500ff11-16cc-496d-aba1-21ce0a190a43 | 0560006e-a1cc-4961-9393-2813b1fa1fb3 |
| B2C tareas CSV | 10efe23e-2b31-4bb5-8147-b56eea422a38 | a98fa1fc-094e-48a8-9f01-6e136743f0a5 |
| Stock compartido CSV | 982dd1cc-7d68-40ea-80d5-40c5cd1a3520 | 8fcce98a-801d-45da-bf32-91b86738233a |

Todas READY; contextos y documentos con SHA-256 consultables en GET.
Resultados reales de esas muestras (costos CLP, no objetivos VROOM):

| Caso/plan | Viable | Pedidos | Metros | Duty segundos | Costo |
|---|---|---|---|---|---|
| B2B manual | Sí | 2 | 2312 | 6694 | 1301.5444 |
| B2B greedy/alternatives | Sí | 2 | 2312 | 3261 | 1206.1833 |
| B2C manual | No, max_tasks | 6 informativos | 10940 | 6453 | 1726.2500 |
| B2C greedy/alternatives | Sí, parcial | 2 | 4024 | 1041 | 1230.1167 |
| Compartido manual CD-A | No, stock | 2 informativos | 5188 | 4613 | 118.0019 |
| Compartido greedy | Sí, parcial | 1 | 2312 | 561 | 103.8703 |
| Compartido alternatives | Sí | 2 | 9325 | 1598 | 213.7639 |

El menor costo greedy del caso compartido **no** demuestra mejor resultado:
omite un pedido. Ni los planes manuales inviables ni diferencias de cobertura
permiten afirmar ahorro. B2B incluye la diferencia convencional de salida/espera.
No son benchmarks de capacidad máxima ni pruebas de optimalidad.

Se conservaron también seis comparaciones de las primeras ejecuciones:
e3670be1-3426-4f87-81b3-8f80d6630990 (escenario da5cd1cd-935c-4c1c-8376-31737f376901),
c6e26738-77bb-4dbb-9fd4-c24949b40781 (edbd5e42-6157-4063-94f9-213fffda22cc),
77eac1a1-78cd-4f23-a6ba-f02b7d4f6fc4 (8a6e6573-b8e4-44fc-a659-8a76f7737673),
666579b5-b0fd-475c-9eb1-d930bccbcb5c (c871244e-e4c7-4e2d-988c-a8f20a2aed9b),
741a7a3d-5801-4557-82ad-80b66c3a8dcf (d91bd179-8ba6-4914-bfc6-e28477b2162b),
aac40599-0bd0-458a-9926-5f5ab384c272 (4f77b655-f53a-4108-81ab-370efc66216d).
No se liberaron reservas de escenarios anteriores. Ninguna de estas nueve
comparaciones crea una corrida/reserva operacional.

## Limitaciones y pendientes

- No óptimo global, ranking automático, evaluación sobre intersección de pedidos,
  baselines históricos sin contexto ni tiempos declarados. UI/exportación:3.3.
- Horario manual fijo al abrir el turno; VROOM puede elegir otra salida. La
  diferencia se informa, no se atribuye exclusivamente a secuencia o política.
- Límites existentes por evaluación:20 pedidos/60 líneas/6 vehículos,80 celdas
  CD–pedido,1024 celdas conservadoras de solver y10,000 posiciones. Dos solver
  calls por comparación más rutas manuales; no se confunde el agregado con una
  sola evaluación ni se extrapola capacidad. Geometrías y variación del solver
  dependen del extracto/perfil fijado y de sus soluciones válidas.
- Los tiempos de job son eventos UTC; no se instrumentaron fases/acuse final
  nuevos ni se presenta una duración de cálculo fabricada. Se mantienen UNKNOWN
  y PARTIAL de 3.1b para corridas operacionales.
- Advertencia conocida Starlette/httpx no bloqueante. No hay defecto funcional
  pendiente identificado en las comprobaciones de esta entrega.

La aplicación local y comparison-worker quedan levantados para revisión por API.
3.2 permanece sin preparar, commit ni push; main/origin y la rama3.1 conservan
el cierre0f95fcf… y las etiquetas no cambian.

## Revisión final y estado Git

Raíz confirmada: `C:\Users\erosi\Desktop\routeops-platform`.
HEAD/main/origin/main y rama 3.1: `0f95fcf507ea219345836cbaeca26e86c1787bd4`.
La rama 3.2 no existe en origin. Verificación directa mediante git ls-remote;
no etiquetas creadas/movidas ni publicación de la candidata. Solo 3.1 se publicó.

21 archivos revisados (12 modificados y 9 nuevos), únicamente fuente, pruebas,
migración, Compose y documentación. Sin secretos/artefactos detectados,
incluyendo revisión de nuevos archivos, whitespace y marcadores de conflicto.
git diff --check aprobado; índice vacío. Estado esperado para revisión:

```text
 M README.md
 M backend/src/routeops/api/main.py
 M backend/src/routeops/infrastructure/persistence/models.py
 M backend/src/routeops/infrastructure/routing/osrm.py
 M backend/tests/integration/test_planning_diagnostics.py
 M docker-compose.yml
 M docs/architecture.md
 M docs/data-model.md
 M docs/milestone-3-1c-diagnostics.md
 M docs/optimization-contract.md
 M docs/product-requirements.md
 M docs/roadmap.md
?? backend/migrations/versions/b82d6c4a910f_add_plan_comparisons.py
?? backend/src/routeops/application/plan_comparison.py
?? backend/src/routeops/application/ports/fixed_route.py
?? backend/src/routeops/infrastructure/persistence/comparison_worker.py
?? backend/src/routeops/infrastructure/persistence/plan_comparisons.py
?? backend/tests/integration/test_m32_http_comparisons.py
?? backend/tests/integration/test_plan_comparisons.py
?? backend/tests/unit/test_plan_comparison.py
?? docs/milestone-3-2-plan-comparison.md
```

Consulta final de disponibilidad: API /health/ready devuelve ready y frontend
HTTP200; la muestra compartida 8fcce98a-801d-45da-bf32-91b86738233a sigue READY.
La migración está aplicada en la base local de desarrollo para esta revisión,
aunque el código 3.2 permanezca sin commit. Los nueve escenarios de prueba
analítica se conservan, junto a todos los datos históricos anteriores.
