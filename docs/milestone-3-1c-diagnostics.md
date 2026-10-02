# Entrega 3.1c — diagnósticos y casos B2B/B2C

Estado: **aceptada, junto con el cierre integral de 3.1; publicación autorizada**.
Fecha: 2026-10-02. Los bloques de estado Git inferiores conservan la revisión
previa a esta aceptación, no el estado posterior al commit de cierre.
Repositorio `C:\Users\erosi\Desktop\routeops-platform`, rama común
`feat/m3-1-operation-analytics`. No se inició 3.2 ni 3.3.

La aceptación incluye restricciones y compatibilidad de 3.1a, costos Decimal
separados del objetivo VROOM, KPIs y tiempos de 3.1b, diagnósticos y casos de
3.1c. Se conservan las pruebas aprobadas y la distinción entre ejecución integral
inicial y correcciones posteriores. La comprobación final de los dos downgrades
confirma guardias independientes de historia, rollback completo y conservación
del head inicial: no se debilitó la inmutabilidad ni se aceptó pérdida de datos.
No hubo cambios funcionales adicionales para este cierre.
El intervalo anterior al COMMIT sigue PARTIAL y el acuse durable UNKNOWN;
la evidencia es automatizada/API, sin nueva revisión visual. Limitaciones y
advertencias se mantienen. La implementación posterior de 3.2 está autorizada,
en su propia rama desde el cierre confirmado, sin iniciar 3.3 ni crear etiquetas.

## Cierre confirmado de 3.1b

Commit `f21effb531a92fe556708989e93a79bde4209588`, autor y committer
Eros Moreno <erosignacio.m@gmail.com>, mensaje
`feat: add plan metrics and durable processing measurements`.
Se revisaron y prepararon exactamente 25 archivos, incluidos nueve nuevos;
`git diff --cached --check` pasó, sin secretos ni artefactos.
La revisión final del alcance temporal pasó seis pruebas de tiempos, Ruff y
mypy estricto sobre 62 módulos. Los resultados previos de 3.1b se conservan.

Tras fetch, `origin/main` seguía en `87f765079c1b03e5e76dfced795a04a32347920b`.
Push de la rama y fast-forward/push de main exitosos; `git ls-remote origin`
confirmó **main y la rama compartida en f21effb…**. Ningún force push ni etiqueta
nueva. v0.1.0, v0.2.0 y v0.2.1 conservaron sus destinos publicados.

La precisión del contrato quedó publicada en 3.1b: `total_elapsed` reconstruye
creación → timestamp de READY/terminal **previo al commit final**, clasificado
`PARTIAL`, derivación `RECONSTRUCTED`. No mide el acuse durable del COMMIT.
`durable_total_elapsed=null`, `UNKNOWN`, motivo `COMMIT_ACK_NOT_RECORDED`.
Cola/esperas reconstruidas, duraciones monotónicas medidas y subtotales
incompletos parciales se distinguen; finales desconocidos permanecen null.
No se rellenaron ni reescribieron resultados históricos.

## Implementación y consulta

`application/diagnostics.py` contiene el catálogo y razonamiento compartidos.
Se explica la respuesta ya reconciliada: no se cambia asignación, rutas,
objetivo del solver, costos, tareas ni cantidades reservadas. Se conserva el
orden prioridad↓/fin de ventana↑/ID↑ y las versiones greedy-v1/alternatives-v2.
La decisión registra ahora también demanda y habilidades observadas; la demo
original agrega candidatos de stock/flota a sus exclusiones sin alterar datos.

Cada razón mantiene el formato existente `code`, `certainty`, `detail`,
`evidence`; su evidencia añade `diagnostic.calculation_version=diagnostics-v1`,
`provenance` y `scope`. Incluye IDs/hashes de revisión/contexto/problema,
decisión/política y/o dataset/checksum de la demo, según la fuente disponible.
No se hacen consultas de inventario actual al reconstruir el diagnóstico.

Los códigos generales de exclusión de asignación siguen siendo principales
para mantener su correspondencia con `decisions.reason_code`. Habilidades y
capacidad aportan causas específicas suplementarias. En omisiones del solver,
una causa local concluyente precede a la observación genérica; esta última
conserva su código y certeza INFERRED. El límite colectivo de tareas es contexto
suplementario, no una atribución individual. `role=PRIMARY/SUPPLEMENTARY` en la
consulta reproduce el orden de las razones del resultado, sin contradicciones.

| API nueva | Contrato |
|---|---|
| GET `/api/v1/runs/{run_id}/diagnostics` | Demo original/revisiones; `offset`, `limit` 1..200, filtros `stage`, `code`, `certainty`; total y next_offset |
| GET `/api/v1/scenarios/{scenario_id}/imports/{batch_id}/diagnostics` | Adaptación paginada de incidencias inmutables; cursor `after`, `limit`; etapa VALIDATION |
| GET `/api/v1/operation-cases` | Cinco nombres reproducibles, catálogo separado de las demos previas |
| POST `/api/v1/operation-cases/{name}/prepare` | Crea siempre un escenario nuevo; carga cinco CSV privados, valida contexto y publica mediante los servicios existentes |

Los diagnósticos de importación conservan código, severidad, dataset, fila,
campo, contrato/validador y hashes del informe/contexto/paquete. No copian el
valor de la celda ni presentan una fila inválida como pedido de una corrida.
Advertencia de validación no equivale a error bloqueante. Códigos de entrada:
el catálogo vigente de 2.1/3.1a, sin renombrarlo; COORDINATE_RANGE se comprobó
por TestClient con dataset orders, fila 2 y campo latitude.

Consulta desconocida: RUN_NOT_FOUND/404. Paginación inválida:
PAGINATION_INVALID/422; filtro inválido: DIAGNOSTIC_FILTER_INVALID/422.
Inconsistencia de hashes o correspondencia: DIAGNOSTIC_FACTS_INVALID/409.
Una corrida pendiente o cancelada antes del resultado no fabrica diagnósticos.
Historias sin documento nuevo usan exclusivamente razones persistidas antiguas,
versión `legacy-recorded-reasons`, sin reinterpretarlas con fixtures actuales.
Un fallo histórico sin razones instrumentadas se declara no disponible.

## Catálogo y certeza

| Código / familia | Etapa | Certeza y alcance |
|---|---|---|
| Códigos del validador existente | VALIDATION | PROVEN: regla estructural/contextual registrada; incidencia separada del run |
| STOCK_NO_FULL_COVERAGE | ALLOCATION | PROVEN solo respecto de la disponibilidad de cada CD en esa decisión y todas las líneas; no implica inviabilidad de una asignación conjunta distinta |
| NO_COMPATIBLE_VEHICLE | ALLOCATION | PROVEN: ninguno de los vehículos evaluados en CDs con stock pasa inclusión de skills y capacidad individual completa |
| NO_SKILL_COMPATIBLE_VEHICLE | ALLOCATION u OPTIMIZATION | PROVEN por inclusión de conjuntos; scope identifica flota/CDs evaluados o únicamente CD ya asignado |
| INDIVIDUAL_CAPACITY_EXCEEDED | ALLOCATION u OPTIMIZATION | PROVEN: cada vehículo compatible por skills falla al menos una dimensión; se registran demanda y capacidades de unidades, peso y volumen; nunca se suman capacidades de vehículos para un pedido |
| WINDOW_SERVICE_SHIFT_INFEASIBLE | OPTIMIZATION | PROVEN si no existe inicio posible aun con viaje cero, considerando ventana de inicio y final de servicio antes del cierre efectivo del turno/CD/horizonte |
| SOLVER_NO_FEASIBLE_ROUTE | OPTIMIZATION | INFERRED: observación de omisión, sin prueba de causa específica o inviabilidad global |
| VEHICLE_LIMITS_CONTEXT | OPTIMIZATION | INFERRED sobre causalidad: registra máximos en metros, segundos y pedidos; no señala automáticamente cuál causó la omisión |
| FLEET_TASK_LIMIT_SHORTFALL | OPTIMIZATION | PROVEN colectivo condicionado a los CDs asignados: pedidos > suma de max_tasks de esa flota; no prueba por qué se omitió un pedido concreto |
| ROUTING_POINT_UNCOVERED, ROUTING_SNAP_TOO_FAR, ROUTING_NO_PATH | OPERATIONAL | PROVEN observación de cobertura vial configurada, separada de falta de stock o outage; mantiene evidencia de snaps |
| ROUTING_DEPENDENCY_FAILED, SOLVER_DEPENDENCY_FAILED | OPERATIONAL | PROVEN fallo de dependencia observado; no demuestra inviabilidad del negocio |
| SOLVER_INPUT_INVALID, SOLVER_RESPONSE_INVALID | OPERATIONAL | PROVEN intercambio rechazado, no decisión válida de no asignación |
| PLANNING_INFRASTRUCTURE_FAILED, RUN_RETRY_LIMIT | OPERATIONAL | PROVEN fallo de procesamiento/recuperación; no se adjunta a pedidos como falta de inventario |

Los códigos existentes de guardias de ejecución/revisión/asignación también
permanecen como observaciones operacionales si impiden procesar una corrida.
No se fabrican incidencias de un pedido cuando solo existe un fallo del run.

### Razonamiento temporal y límites

Por vehículo compatible del CD fijo:
`earliest=max(window_start,effective_shift_start)` y
`latest=min(window_end,effective_shift_end-service_seconds)`.
Si earliest > latest en todos, el servicio no cabe incluso con viaje cero.
Supuestos explícitos: viaje no negativo, pedido indivisible y jornada efectiva
que intersecta turno, horario del CD y horizonte. El inicio de servicio puede
coincidir con window_end; el servicio debe terminar antes de effective_shift_end.
Una igualdad exacta es posible, no prueba de imposibilidad; se probó el límite
exacto y su superación por un segundo.

No se usa una ruta directa/estimación OSRM como cota inferior universal.
Distancia y conducción siguen mapeadas a VROOM y reconciliadas en 3.1a. En
3.1c se registran como contexto; no se pretende demostrar su causalidad por
la sola omisión. Capacidad colectiva de tareas: para flota con max_tasks
definido en todos los vehículos, al menos `N-sum(max_tasks)` no pueden rutearse
juntos desde ese CD. Con un vehículo sin máximo no se inventa ese bound.

Stock consumido por decisiones anteriores: `prior_policy_consumption` compara
available_before con stock inicial menos reservas externas, seguridad y
reservas RouteOps previas. Se mantiene el snapshot, secuencia y candidatos.
La demo greedy compartida prueba agotamiento condicionado a decisiones previas;
alternatives-v2 conserva su asignación conjunta conocida. Ninguna prueba atribuye
optimalidad global a la política o a VROOM.

## Persistencia, recuperación y migración

Migración `a71c9e3d602b`, dependiente de `f6b2d8a4c190`:
`planning_diagnostics`, PK/FK run_id RESTRICT, versión, SHA-256, JSONB,
token/número de intento interno y created_at UTC. Es un documento completo,
no incidencias parciales. La tabla se incorpora al metadata SQLAlchemy.

Restricciones de estructura JSON, hash y número de intento; trigger BEFORE
INSERT/UPDATE/DELETE con función propia:

- UPDATE/DELETE prohibidos; una sola fila por run impide duplicados.
- Orden job → run al comprobar propietario; la orquestación mantiene escenario
  → job → run. Se comprueban token, intento y concesión vigente. Recuperación
  sin token no puede insertar mientras exista propietario vigente.
- Se inserta mientras el procesamiento sigue abierto; el JSON de no asignados
  debe coincidir exactamente con result_data. Un fallo debe coincidir con
  run.error, no tener resultado ni pedidos no asignados inventados.
- Resultado, filas de rutas/excepciones, liberación de pedidos no ruteados,
  mediciones, documento y evento READY/FAILED comparten la transacción.
  Un fallo posterior a insertar el documento revierte todo; el manejador de
  fallo guarda solo el documento operacional y compensa los HELD de esa corrida.
- Consultas REPEATABLE READ verifican hash/correspondencia y nunca escriben.
  Aceptar/cancelar conserva documento y plan; estado/reservas actuales separados.
- Sin backfill de corridas/revisiones/telemetría ni cambios a reservas externas.
- Downgrade permitido solo con tabla vacía, elimina tabla/función/trigger propios,
  preserva objetos previos y PostGIS. Guardias independientes de tiempos y
  diagnósticos rechazan pérdida de historia y revierten toda la migración.

Las excepciones de los puertos de routing/solver están en application/ports;
los imports previos de infraestructura las reexportan para compatibilidad.
Las demos nuevas guardan códigos seguros de fallo, no el texto de excepciones
con rutas/URLs. VROOM continúa exclusivamente por SolverGateway. Ninguna
llamada externa se incorporó a una transacción que bloquee inventario.

Durante el desarrollo se sincronizaron la nueva CHECK y la función de la
migración aún no publicada en el entorno local (tabla nueva con **0 filas**),
sin eliminar ni cambiar filas. Las bases descartables probaron upgrade desde
cero/downgrade usando la migración final; alembic check local no detecta diferencias.

## Casos reproducibles y resultados observados

Fecha de planificación 2026-10-15, IANA America/Santiago, horizonte 08:00–18:00,
CLP; puntos del extracto de Santiago existente. Cada SKU tiene on_hand=200,
reserva externa=5, seguridad=5; RouteOps parte sin reservas en el escenario
nuevo. Cada pedido es una tarea, aunque comparta coordenadas con otro.

| Caso | Datos/control | Propiedad verificada con VROOM/OSRM |
|---|---|---|
| b2b-feasible | 2 pedidos, 12 y 8 unidades de 20 kg/0.02 m³; camión 40u/500kg/2m³, cold; servicios 30/20 min | 2 ruteados, ventanas/capacidad cumplidas, HELD → CONFIRMED por HTTP |
| b2b-diagnostics | 6 pedidos: OK, hazmat ausente, 600kg, 3m³, 41u, servicio 120min iniciado desde 17:00 | 1 ruteado; skills y cada dimensión excluidos en asignación; servicio imposible PROVEN en solver; no reservas para excluidos y todas las líneas del no ruteado RELEASED |
| b2c-feasible | 6 pedidos de 1u/0.1kg/0.001m³, dos coordenadas compartidas, 5min, ventanas 09:00–13:00, prioridades 100..50; van, parcel, max_tasks=6 | 6 pedidos distintos ruteados; recorrido reconciliado, no mezcla IDs por coordenada |
| b2c-task-pressure | Mismos 6 pedidos, max_tasks=2 | 2 ruteados y 4 omitidos; omisión individual INFERRED, déficit colectivo 4 PROVEN; 4 reservas completas liberadas |
| b2c-distance-inferred | Mismos 6 pedidos, max_tasks=6, distancia=1m y conducción=1s | 0 rutas, 6 reservas completas liberadas; razones/causalidad INFERRED, sin inventar cuál límite fue determinante |

Los casos B2B/B2C controlan un CD para aislar causas; las demos anteriores
conservan la evaluación multiorigen/cobertura stock completa. No hay arquitectura
por etiqueta, muelles, paletización, entregas divididas ni optimización nueva.
No se exige una secuencia exacta de ruta ni qué pedidos particulares el solver
prefiere cuando existen varias soluciones. No se cambió el extracto ni límites.
El caso mayor usa 6 pedidos/6 líneas/1 vehículo/1 CD: 6 celdas CD–pedido y cota
conservadora de 64 celdas del trabajo del solver, dentro de los límites actuales.

### Generar archivos e importar mediante la interfaz existente

```powershell
docker compose exec -T backend python -m routeops.infrastructure.data.operation_cases --case b2b-diagnostics --format csv --output /tmp/m31-b2b-csv
docker compose exec -T backend python -m routeops.infrastructure.data.operation_cases --case b2c-task-pressure --format xlsx --output /tmp/m31-b2c-xlsx
docker cp routeops-backend-1:/tmp/m31-b2b-csv C:\Users\erosi\Desktop\routeops-operation-b2b
docker cp routeops-backend-1:/tmp/m31-b2c-xlsx C:\Users\erosi\Desktop\routeops-operation-b2c
```

Usar directorios nuevos. El generador rehúsa un destino no vacío, salida 2;
no sobrescribe silenciosamente. En `/imports`, crear escenario nuevo, cargar
los cinco CSV o workbook.xlsx, fijar el contexto indicado, validar y publicar.
Después seleccionar revisión en `/planning`. La pantalla analítica detallada
y selector de estos casos dentro del catálogo visual corresponden a 3.3.

Preparación equivalente por API:

```powershell
$base = 'http://127.0.0.1:8000/api/v1'
$prepared = Invoke-RestMethod -Method Post -Uri "$base/operation-cases/b2b-diagnostics/prepare"
$key = [guid]::NewGuid().ToString('N')
$path = "$base/scenarios/$($prepared.scenario_id)/revisions/1/runs"
$run = Invoke-RestMethod -Method Post -Uri $path -Headers @{'Idempotency-Key'=$key} -ContentType 'application/json' -Body '{"solution_quality":"BALANCED","allocation_policy":"alternatives-v2"}'
Invoke-RestMethod -Uri "$base/revision-runs/$($run.run_id)"
Invoke-RestMethod -Uri "$base/runs/$($run.run_id)/diagnostics?limit=100"
Invoke-RestMethod -Uri "$base/runs/$($run.run_id)/metrics"
```

Consultar estado hasta READY/FAILED. Reutilizar $key recupera la misma corrida;
una nueva preparación crea stock independiente. No ejecutar acciones sobre
escenarios históricos. Los nombres permitidos están en CASE_NAMES y GET catálogo.

### Corridas HTTP persistidas de esta aceptación

| Caso | Escenario | Corrida | Estado final observado |
|---|---|---|---|
| b2b-feasible (XLSX) | 8cfc066e-2846-49ee-8dc5-840154c9f4bf | 779e3dc1-4773-4675-b25d-1f0c3cbba22c | ACCEPTED, 2 ruteados, CONFIRMED |
| b2b-diagnostics (CSV) | 307eab24-47c8-410a-8456-f20e6a031d06 | 29af2545-0557-4f4c-80e0-3fbd3f385c17 | CANCELED, plan histórico 1 ruteado, RELEASED |
| b2c-feasible (CSV) | 351e4db9-2276-4866-b70b-64f333855e32 | f58b1c89-12d3-4793-ba96-598d82bccbca | CANCELED, plan histórico 6 ruteados |
| b2c-task-pressure (XLSX) | f8e8385a-b7ba-4c58-b2a7-0b5954728193 | 2eb8f9da-4dd1-4342-adf6-97dcbd09f931 | CANCELED, plan histórico 2 ruteados/4 omitidos |
| b2c-distance-inferred (CSV) | 697ab626-386f-46e6-b267-5a418cb1a8e6 | 5d8a419d-1f85-4ae1-903f-54b5e237eb9f | CANCELED, plan histórico 0 rutas/6 omitidos |

Dos preparaciones adicionales de b2b-feasible comprueban escenarios distintos,
sin corridas ni reservas. Las bases de los ensayos PostGIS se eliminan al finalizar.
Las reservas/revisiones/corridas locales anteriores no se eliminaron ni liberaron.

## Validación integral conjunta de 3.1a–c

Entorno Docker Desktop/Linux; Python 3.14.7, PostgreSQL18/PostGIS3.6,
VROOM1.15.0, adaptador1.2.0, OSRM26.9.0; frontend Node24.21.0. Locks existentes.
Contenedor de pruebas propio con backend montado, tools dev declaradas; dentro
de Compose URLs osrm:5000/vroom:3000 y backend:8000; Windows usa 127.0.0.1.
No se imprimieron secretos de configuración.

| Comprobación | Resultado real |
|---|---|
| Suite backend completa con DB y HTTP habilitados | 277 casos ejecutados: 275 aprobados, 2 fallos de expectativas antiguas de downgrade; 156.95s, sin skips |
| Corrección y repetición afectada | 5 aprobadas en 6.15s: los dos fallos resueltos, nueva guardia de tiempos sin diagnósticos y aserciones reforzadas de fencing/incidencia |
| Estado final de cobertura | **278 casos backend distintos aprobados: 180 unitarios, 98 integraciones**, confirmados por colección; no se suman repeticiones solapadas |
| Desarrollo dirigido anterior | 28 unitarias aprobadas (22 nuevas + 6 existentes), 10 integraciones nuevas aprobadas; ya incluidas en el gate integral |
| Migraciones | Upgrade/head, downgrade vacío/inmutable y preservación de PostGIS; alembic current a71c9e3d602b y check sin operaciones nuevas |
| HTTP | Incluye idempotencia/concurrencia de cargas/corridas, conflictos de contenido, publicación, aceptación/cancelación, las cuatro demos previas y seis pruebas nuevas de casos/consulta/preparación |
| Original real | Smoke 8e0fdd67-10b3-400c-86d4-1ed05a4b967a: PARTIAL, dos rutas, cuatro entregas; ORD-003 STOCK_NO_FULL_COVERAGE, 20 SKU-C frente a 5/CD |
| Restricciones/costos/KPIs/reservas | Límites exactos/exceso, validación de payload y conciliación, Decimal con tarifas persistidas separado del objetivo, utilización/ventanas/cobertura, compensación y stock externo intacto: pruebas unitarias/integraciones completas |
| Recuperación/historia | Concesión recuperada, trabajador obsoleto, rollback, corrida/reservas idempotentes, consultas inmutables y cancelación/aceptación sin modificar plan/diagnósticos |
| CLI real | Plantillas CSV/XLSX y casos b2b-diagnostics CSV / b2c-task-pressure XLSX: generación y validación salida 0, datos/encabezados deterministas, destino existente salida 2 y hashes intactos; directorios descartables limpiados |
| Ruff / mypy estricto | Aprobados migrations/src/tests; **66 módulos** |
| Frontend integral y compatibilidad adicional | Suite existente **27 aprobadas** en 4.25s; después **6 aprobadas** de PlanningWorkspace con un caso nuevo de código/certeza: **28 distintos** |
| TypeScript / build / Compose | Aprobados; build final 640ms, advertencia conocida de bundle, config quiet aprobado |

Los dos fallos no se marcaron aprobados: uno hardcodeaba el head anterior después
de un rollback completo; ahora compara la versión inicial con la final. El otro
esperaba llegar a la guardia de tiempos, pero la nueva guardia de diagnósticos
rechaza antes. Se comprueban ambas de forma independiente. No se omitieron ni
eliminaron pruebas. Tras estos ajustes de tests se repitió únicamente lo afectado.

La primera repetición frontend ejecutó la imagen anterior (5 pruebas de ese
módulo); no se contabilizó como comprobación del nuevo caso. Se reconstruyó la
imagen y se confirmaron las **6 pruebas**, TypeScript y build actuales.

La comprobación CLI valida estructura; las pruebas de publicación contextual
CSV/XLSX y solver son distintas. La evidencia de esta entrega es **automatizada
y API**, no una revisión nueva en navegador. No se declara haber usado mouse,
teclado, navegador ni tomado capturas. Se conserva la revisión visual aprobada
de v0.2.1 como antecedente para componentes visuales sin cambios.

Muestra funcional del original: 8,218m; conducción897s, espera0s, servicio2,700s,
duty3,597s; objetivo VROOM4,766,044 escala100, separado del costo operativo.
No es una medición de capacidad máxima ni un objetivo nuevo de rendimiento.

## Limitaciones y pendientes

- No prueba de óptimo global ni inviabilidad conjunta cuando solo se analizó
  una asignación fija. Distancia/conducción y packing mantienen atribución
  conservadora; no se inventan cotas OSRM ni causales para cada omisión.
- Casos pequeños controlados; siguen límites medidos del solver, importación
  separada y cobertura vial actual. No se extrapola rendimiento.
- La pantalla vigente muestra la razón principal y su certeza; el catálogo
  completo/evidencia suplementaria se consulta por API. UI analítica/exportación
  permanecen 3.3; comparaciones/manual 3.2 no se implementaron.
- Acuse SQL final/HTTP no medido, intervalos perdidos e historia insuficiente
  permanecen desconocidos, conforme al cierre 3.1b.
- Advertencias conocidas no bloqueantes: Starlette/httpx en TestClient y bundle
  Vite de 1,859.49kB (gzip522.19kB). No aparecieron defectos funcionales pendientes.

Se revisaron diff y archivos nuevos, sin secretos ni artefactos. HEAD y referencias
publicadas permanecen en el cierre 3.1b; los cambios de **3.1c no están preparados,
no tienen commit ni push**, etiquetas intactas. Aplicación local disponible para revisión.

Comprobación final directa: frontend `http://127.0.0.1:5173/` HTTP200 y
`/health/ready` estado ready. Todos los servicios siguen levantados, con puertos
publicados únicamente en 127.0.0.1. `git ls-remote` confirma main y la rama en
f21effb531a92fe556708989e93a79bde4209588; destinos previos preservados:
v0.1.0 → 449e671822622919594fb5fa73e633cf32cf64c2,
v0.2.0 → 4a99f99708f9b4a31bf9d699e4b2ec91238c7021,
v0.2.1 → 0241a5168d9f7c02a152cf3614bf502659ea33b3.
El contenedor propio de pruebas se retira al terminar; no se eliminan servicios,
volúmenes ni registros de la aplicación.

## Archivos y estado Git final

29 archivos: 20 modificados y 9 nuevos; todos código, pruebas, migración o documentación.
Índice vacío. `git diff --check` y comprobación adicional de archivos nuevos aprobados;
sin conflictos, whitespace final, secretos detectados ni archivos generados.
Archivo mayor: 149,549 bytes (prueba de integración existente).

```text
 M README.md
 M backend/src/routeops/api/main.py
 M backend/src/routeops/application/planning.py
 M backend/src/routeops/domain/policies/allocation.py
 M backend/src/routeops/domain/policies/operational_allocation.py
 M backend/src/routeops/infrastructure/data/demo_catalog.py
 M backend/src/routeops/infrastructure/persistence/models.py
 M backend/src/routeops/infrastructure/persistence/repository.py
 M backend/src/routeops/infrastructure/persistence/revision_runs.py
 M backend/src/routeops/infrastructure/routing/errors.py
 M backend/src/routeops/infrastructure/solver/errors.py
 M backend/tests/integration/test_persistence_migration.py
 M docs/architecture.md
 M docs/data-model.md
 M docs/milestone-3-1a-contracts-costs.md
 M docs/milestone-3-1b-metrics.md
 M docs/optimization-contract.md
 M docs/product-requirements.md
 M docs/roadmap.md
 M frontend/src/PlanningWorkspace.test.tsx
?? backend/migrations/versions/a71c9e3d602b_add_planning_diagnostics.py
?? backend/src/routeops/application/diagnostics.py
?? backend/src/routeops/application/ports/errors.py
?? backend/src/routeops/infrastructure/data/operation_cases.py
?? backend/src/routeops/infrastructure/persistence/diagnostics.py
?? backend/tests/integration/test_m31_http_diagnostics.py
?? backend/tests/integration/test_planning_diagnostics.py
?? backend/tests/unit/test_diagnostics.py
?? docs/milestone-3-1c-diagnostics.md
```
