# Entrega 3.1a — contratos, restricciones y base de costos

Estado: **aceptada formalmente; cierre autorizado el 2026-10-02**. Base publicada
`v0.2.1`/`main`: `0241a5168d9f7c02a152cf3614bf502659ea33b3`.
Rama común de 3.1: `feat/m3-1-operation-analytics`. Este informe corresponde
exclusivamente a 3.1a; 3.1b se desarrolla después de su cierre y 3.1c sigue pendiente.
La aceptación conserva las comprobaciones documentadas abajo; el cierre solo
actualiza documentación y no repite pruebas sin un cambio funcional.

## Diseño vigente y alcance

Los Hitos 1–2 y la corrección v0.2.1 están aceptados. README, PRD,
arquitectura, modelo de datos, contratos, ADR-0001 y roadmap ahora distinguen
capacidades actuales de ideas del diseño inicial. Los informes de aceptación
2.5/2.6/v0.2.1 se conservan como evidencia histórica, sin reescribir resultados.

B2B y B2C usan escenario/revisión, pedidos completos, líneas, flota e inventario
comunes. Una etiqueta comercial no implica prioridades, tiempos, skills,
vehículos ni restricciones diferentes: esos valores deben aparecer en los
contratos. Los casos de 3.1c serán sintéticos e independientes.

Se excluyen entregas divididas, transferencias, paletización, citas de muelle,
devoluciones, seguimiento de conductores, POD, tráfico en vivo e integraciones
externas. Sin autenticación, servicios nuevos ni otro solver. VROOM se utiliza
solo mediante `SolverGateway`; RouteEngine es otro proyecto. Las políticas
`greedy-v1`/`alternatives-v2`, los datos originales y el extracto OSRM no cambian.

## Matriz trazable del contrato v1

Referencias de código: `application/import_validation.py` (estructural/contexto),
`application/revision_problem.py` (revisión → DTO),
`domain/optimization/validation.py` (guardia v1), `infrastructure/solver/vroom.py`
(mapeo) y `application/optimization_reconciliation.py` (respuesta). Las rutas
de pruebas de esta tabla son relativas a `backend/tests`.

| Regla / entrada | Validación y representación interna | VROOM | Reconciliación de respuesta | Evidencia automatizada |
|---|---|---|---|---|
| Unidades: líneas.quantity / vehicles.capacity_units | Enteros positivos; demanda total completa; `Capacity.units`, límite individual 2,147,483,647 | delivery/capacity[0] | Suma de pedidos cabe; carga inicial igual a demanda completa y resta cada entrega; final cero | `unit/test_optimization_contract_v1.py`: load/capacity; `integration/test_persistence_migration.py`: reservas multilínea y rollback |
| Peso: líneas.unit_weight_kg / vehicles.capacity_weight_kg | Decimal finito; cantidad × kg ×1,000 exacto, sin redondeo; `weight_grams` | delivery/capacity[1] | Las tres dimensiones de carga se reconstruyen | `unit/test_operational_allocation.py`: exact_published_precision; integración published_precision; contrato capacity/load |
| Volumen: líneas.unit_volume_m3 / vehicles.capacity_volume_m3 | Decimal finito; cantidad × m³ ×1,000,000 exacto; `volume_cm3` | delivery/capacity[2] | Igual guardia tridimensional | Pruebas de precisión/publicación y contrato anteriores; `unit/test_vroom_adapter.py`: array [2,2000,4000] |
| Skills: orders.required_skills / vehicles.skills | Slugs normalizados; inclusión de todos los requeridos; frozenset | Enteros deterministas de namespace **user**, separado de namespace **center** | Inclusión en skills del vehículo asignado | `unit/test_optimization_contract_v1.py`: skills y colisión de namespaces; `unit/test_operational_allocation.py`: flota |
| Ventanas y servicio | Offsets absolutos, una jornada contextual; `DeliveryTask` con segundos enteros, prioridad 0–100; import service_minutes 1–1440 | time_windows, service, priority | Inicio de servicio dentro de intervalo inclusivo; servicio exacto; arrival + wait = service_start; + service = departure | `unit/test_import_context.py`: offset/horizon/DST; contrato window/service/arrival; integración real con espera forzada |
| Jornada, CD y horizonte | Locales según fecha/IANA; rechaza folds/gaps DST; ejecución intersecta jornada con CD y horizonte; `OptimizationVehicle.shift_*` | time_window del vehículo, instantes UTC relativos al horizonte | Salida/retorno dentro de jornada; cronología de todas las piernas y duración total | Contexto local_hours/DST; contrato shift/window/fractional_instant; integración fractional_time antes de corrida/reservas |
| Distancia máxima de ruta | `vehicles.max_route_distance_meters`, entero opcional 1..2,147,483,647; incluye salida y regreso al CD | `max_distance` en metros | `route.totals.distance_meters ≤ límite`; igualdad permitida | CSV/XLSX antiguos y nuevos, límite exacto y superación; integración VROOM real |
| Conducción máxima | `vehicles.max_driving_seconds`, entero opcional 1..2,147,483,647; excluye espera y servicio | `max_travel_time` en segundos | `route.totals.driving_seconds ≤ límite`; igualdad permitida | Contrato y VROOM real; fallo del solver compensa reservas |
| Máximo de pedidos | `vehicles.max_delivery_tasks`, entero opcional 1..2,147,483,647; cada pedido completo equivale a una tarea | `max_tasks`; START/END no cuentan | Número de pasos DELIVERY ≤ límite | Dos tareas con `max_tasks=1` en VROOM real; respuesta adulterada se rechaza |
| Prioridad | Entero 0–100; orden de asignación prioridad↓, fin de ventana↑, ID↑ | priority | Se comprueba identidad de cada pedido; no se inventa garantía de optimalidad/prioridad global del solver | `unit/test_allocation.py`: consumes_stock_in_priority_order; contrato priority; mapper priority=80 |
| Pedido completo / aislamiento de CD | Todas las líneas desde un CD; UUID task único por pedido; candidatos/evidencia y reservas atómicas | Job delivery completo + skill center no colisionable | Un pedido una vez, centro correcto, nunca fracciones ni cruces | `unit/test_operational_allocation.py`: exclusive_stock_never_splits; contrato unknown_job/missing_task/extra_route; integración concurrent allocations/runs |
| Inicio y retorno al CD | start == end, coordenadas de CD asignado | start y end explícitos | START/END, posiciones del centro y extremos de geometría | Contrato open_route/center/geometry; integración real cuatro demos y original |
| Precisión/rangos | Decimal en SQL; escala peso1,000/volumen1,000,000; costos de revisión 10,000, demo100; no floats monetarios; segundos sin fracción | Enteros; tipos estrictos, sin truncar floats/strings/bools | Métricas no negativas; pasos, totales y summary coinciden; moneda/escala comunes | Contrato fractional_metric/string_metric/boolean_metric/rate_overflow/capacity_overflow; `unit/test_operating_cost.py`: límites/precisión |
| Cobertura vial v0.2.1 | OSRM table con evidencia original/snap; máximo configurable 250m antes de stock | Mismo OSRM car y checksum; geometría solicitada | Geometría cubre stops/extremos≤250m; entrega conserva posición≤2m | `unit/test_osrm_coverage.py`: exceso/sin ruta/outage separados; integración uncovered_point sin reservas; contrato geometry/coordinates/corrupt_polyline |
| Integridad de hechos de ruta | DTO neutral, validado independientemente del JSON proveedor | Solo jobs, sin setup/breaks en v1 | Secuencia, cronología, carga, tiempos, sumas, objetivo y summary; rechaza violations y métricas acumuladas decrecientes | Contrato route_total/summary/duration/distance/break/setup/violations; servicio demo usa una fixture completa en lugar de ruta falsa sin pasos |

Los tests negativos mutan una respuesta válida con servicio y espera calculables;
comprueban que no puede convertirse en un resultado aceptado. La conciliación
se comparte entre el adaptador, la demo original y el coordinador de revisiones.
Se conserva la compensación existente ante `SolverResponseError`.

Una validación estructural o contextual de importación no garantiza que una
revisión sea ejecutable. La precisión de instantes del solver se comprueba antes
de crear la corrida: `RUN_TIME_PRECISION_INVALID`, HTTP422, sin reservas. No se
cambia retrospectivamente el contrato/versión de los informes ya publicados.

La prueba de coordenadas usa floats exclusivamente para geometría/distancia
geográfica, nunca dinero. El control de cobertura mantiene el método existente
de distancia a vértices de geometría; no afirma comprobar cada metro del trazado.
Sin solicitud de geometría no hay prueba de cobertura ni distancia de piernas;
los flujos normales sí la solicitan. La distancia total y el objetivo son
obligatorios: su ausencia no se sustituye por cero.

## Límites por vehículo y compatibilidad

El proveedor fijado es VROOM 1.15.0 mediante vroom-express 0.12.0; OSRM 26.9.0.
No se cambiaron imágenes ni perfil. El [API de la etiqueta v1.15.0](https://github.com/VROOM-Project/vroom/blob/v1.15.0/docs/API.md)
define límites por vehículo y separa costos de conducción y tareas. El
[constructor del vehículo](https://github.com/VROOM-Project/vroom/blob/v1.15.0/src/structures/vroom/vehicle.cpp)
conserva máximos opcionales y sus defaults internos. Los tres campos de la
matriz están implementados en CSV/XLSX, `OptimizationVehicle`, SQL, payload y
conciliación. Vacío o ausente significa `NULL`: no añade otro límite, pero sí
persisten jornada, horizonte y retorno al centro. Cero, negativos, fracciones y
valores superiores a 2,147,483,647 se rechazan. Un pedido con varias líneas
constituye una tarea; dos pedidos en una coordenada constituyen dos tareas.

Las nuevas cargas fijan contrato de archivos **2.2**, validador **3.1a.1** y
DTO de optimización **1.1** si algún vehículo lleva límites. Los archivos con
encabezados antiguos siguen siendo válidos en 2.2 y sus límites se guardan NULL.
El adaptador VROOM informa versión **1.2.0** en resultados nuevos para distinguir
su nuevo mapeo; la metadata de resultados históricos no se modifica.
El replay de lotes 2.1/2.3b.1 utiliza la lista anterior de columnas y genera
el mismo informe normalizado y SHA. La UI usa la versión del lote para solicitar
la validación de un lote antiguo aún provisional. Los originales, hashes de
paquete, contextos y revisiones publicados no se reescriben. El hash de fila
de una carga nueva incorpora solo los campos opcionales presentes con valor.
La migración `e3a1b7c9d240` añade columnas nullable y CHECK a `vehicles`;
rehúsa downgrade si perdería límites de una revisión publicada. No altera
triggers de inmutabilidad ni reservas. La provenance por fila se mantiene.

La política de asignación de CD no cambia. La factibilidad individual de flota
no garantiza que todos los pedidos quepan en una ruta. Un pedido sin ruta recibe
`SOLVER_NO_FEASIBLE_ROUTE` con certeza `INFERRED`; 3.1c investigará motivos
concluyentes sin atribuir por defecto el fallo a un límite. `max_travel_time`
es conducción, no duración de trabajo; servicio y espera siguen consumiendo
jornada/horizonte. El objetivo VROOM mantiene su mapeo actual.

## Costo de negocio versus objetivo entero

### Base implementada

`GET /api/v1/runs/{run_id}/estimated-operating-cost` calcula desde hechos
persistidos, sin llamada a OSRM/VROOM y sin escritura:

```text
duty_seconds = driving_seconds + service_seconds + waiting_seconds
fixed = fixed_rate, únicamente para vehículos con ruta utilizada
duty = hourly_rate × duty_seconds / 3,600
distance = km_rate × distance_meters / 1,000
route_cost = round4(fixed) + round4(duty) + round4(distance)
run_cost = suma de route_cost
```

No se cobra el tiempo antes de salir ni después del retorno, la totalidad de
la jornada, reservas de stock ni una tarifa de vehículo sin ruta. Es un costo
estimado operativo: no incluye impuestos, peajes, ingresos ni costos reales.
No cambia qué solver/política elige una ruta.

Se usan exclusivamente `Decimal`, con contexto local de 60 dígitos para las
divisiones (horas pueden ser periódicas). Los productos de entrada acotados se
representan exactamente; las fracciones se redondean una sola vez **por componente
y ruta**, a cuatro decimales, `ROUND_HALF_UP`. No hay redondeo previo de horas/km.
Se suman esos componentes redondeados para que tabla y total coincidan. Los
montos JSON son strings decimales. Una futura UI puede mostrar dos cifras, pero
ese redondeo de presentación no sustituirá el valor de cuatro cifras ni cambiará
por sí solo la precisión de CLP. El catálogo/UI definitivo pertenece a 3.1b/3.3.

Ejemplo verificable: fixed 10, hora 36, km 2, distancia 2,500m,
conducción 1,200s + servicio 600s + espera 1,800s: total **51.0000**.
Una flota sin rutas produce **0.0000**, aun teniendo tarifas fijas no nulas.
Tarifas importadas máximas 214,748.3647 (×10,000≤int32), precisión 4; hechos por
ruta acotados a int32. La prueba de límite obtiene **589271205443.0629**, sin
overflow ni float monetario. El rango matemático probado no amplía el límite
de trabajo operativo medido ni promete factibilidad física. La demo conserva
su propio rango de tarifa con escala 100 (hasta 21,474,836.47), también probado;
la consulta respeta la escala del resultado al comprobar representabilidad.

### Procedencia e historia

- `calculation_version`: `operating-cost-v1`.
- Rutas: `optimized_routes.payload`, comprobado contra JSON de resultado y
  columnas persistidas de distancia/duración. Tarifas: misma revisión de
  escenario, inmutable y con hash de contenido/contexto.
- Demos originales nuevas guardan tarifas decimales en input.operating_cost_rates.
  Una demo histórica que no las registró devuelve `COST_RATES_NOT_RECORDED` 409;
  no usa silenciosamente la fixture actual para revalorizar su historia.
- El resultado identifica run/revisión, origen, SHA-256 de hechos y tarifas.
  Repetir la consulta/aceptar una corrida no cambia esos hechos o el cálculo.
- Sin resultado: `COST_RESULT_NOT_AVAILABLE` 409; corrida inexistente 404;
  hechos/tarifas inconsistentes: códigos COST_* estables. No hay rutas físicas
  ni datos privados en errores. No se añade ninguna tabla o migración.
- No se modifican KPIs previos; **kpis.estimated_cost sigue siendo el objetivo
  histórico del solver**, no la nueva cifra de negocio.

### Diferencia con VROOM

El mapeo vigente envía fixed, per_hour y per_km. La [implementación de costos](https://github.com/VROOM-Project/vroom/blob/v1.15.0/src/structures/vroom/cost_wrapper.cpp)
combina conducción y distancia y redondea a unidades enteras. El
[formateador/evaluador de rutas](https://github.com/VROOM-Project/vroom/blob/v1.15.0/src/utils/helpers.cpp)
añade fijo solo a rutas utilizadas y tareas mediante una tarifa separada.
RouteOps no envía per_task_hour, por lo que el servicio no aporta ese costo;
la espera no participa de este objetivo. Se conserva el campo histórico
per_duty_hour_units y su mapeo para no modificar silenciosamente la búsqueda.

La respuesta devuelve por separado `solver_objective.units`, `scale` y `currency`.
Escala original 100 y revisión 10,000. Puede diferir del costo de negocio tanto
por componentes como por redondeo interno; **no son cifras equivalentes**.
La prueba real fuerza dos ventanas separadas, servicio 1,200s y espera ≥3,600s:
el costo de negocio supera al proxy de conducción/fijo/distancia en más de 36.
Cambiar per_task_hour o el objetivo necesita decisión explícita, nuevos tests
y comparación; no está incluido en 3.1a.

## Preparación para 3.1b — fórmulas y decisiones

Nada de este catálogo adicional se implementa aquí. Cada indicador deberá
identificar versión, unidad, fuente, población y denominador.

| Indicador | Fórmula propuesta / decisión |
|---|---|
| Utilización máxima por ruta y dimensión | max(carga a bordo en START/DELIVERY/END) / capacidad; incluye carga inicial; resultado independiente para unidades, gramos y cm³; capacidad nula/cero → null |
| Utilización promedio por duración | Σ(carga a bordo × segundos de intervalo) / (capacidad × duración operativa de ruta), por separado para unidades, gramos y cm³. Incluye conducción, espera y servicio; duración/capacidad nula o cero → null |
| Ventanas | Servicio iniciado dentro del intervalo inclusivo / entregas ruteadas con ventana; espera temprana que termina dentro cumple. No usar llegada como incumplimiento. Sin denominador → null |
| Balance de trabajo | Min/max/media y desviación estándar poblacional de duty por vehículo utilizado; CV=std/media. CV null si <2 vehículos o media 0. Presentar población usada y no mezclar vehículos sin ruta con carga cero |
| Costos y ratios | Base operating-cost-v1; costo/pedido ruteado y km/pedido → null sin pedidos. Mantener objetivo entero como métrica distinta |
| Tiempos de cálculo | Medir por separado cola inicial, fases activas de cada intento, espera entre reintentos y recuperación, y total creado→READY/terminal de procesamiento. Ninguna fase ausente se fabrica a partir de resta de marcas parciales |
| Cobertura/asignación | Pedidos válidos, asignados a CD, ruteados y excepciones por etapa; tasas con denominadores explícitos; no confundir asignación de stock con entrega ruteada |

Para el promedio, la carga de un pedido permanece a bordo durante su servicio
y se descuenta **después**. La `load_after` que entrega VROOM describe la carga
posterior al paso, no la carga del intervalo que culmina en ese servicio. Así,
conducción hasta una entrega, espera y servicio utilizan la carga anterior;
el intervalo siguiente usa `load_after`. El retorno al CD usa la carga restante.
Ejemplo en unidades: capacidad 10, carga inicial 8; 600 s conducción + 300 s
espera + 300 s servicio con carga 8; luego 600 s conducción + 300 s servicio
con carga 4; retorno 300 s con carga 0. Duración 2,400 s, numerador
`8×1,200 + 4×900 + 0×300 = 13,200` unidad·s, promedio `13,200/(10×2,400)
= 0.55` (55 %) y máximo `8/10 = 0.8` (80 %). Se calcula igual, por separado,
para peso y volumen; nunca se promedian paradas o dimensiones entre sí.

La cola inicial va de creación a primer inicio de trabajo. Cada intento guarda
inicio, fin y fases instrumentadas: cobertura/OSRM, asignación y reserva,
solver/VROOM, conciliación y persistencia. Los intentos fallidos cuentan; la
espera entre intentos y el lapso de recuperación se informan aparte. Tras una
interrupción sin marca de fin no se conoce el instante real en que cesó el
trabajo: la duración de esa fase es `null`, no el vencimiento de su concesión.
El total se mide directamente desde creación hasta READY o un estado terminal
de procesamiento; no se calcula sumando fases que pueden solaparse. Polling
del navegador y espera humana para aceptar/cancelar quedan fuera; tampoco se
confunden estos tiempos de plataforma con conducción/servicio/espera de ruta.

Estas fórmulas quedan **definidas para 3.1b, no implementadas**. También queda
para 3.1b decidir la presentación monetaria y crear el catálogo de KPIs.
Objetivos de rendimiento existentes son muestras locales de 2.6, no objetivos
nuevos inventados para analítica. No se extrapola a límites máximos.

## Preparación para 3.1c — casos independientes

1. **B2B sintético:** pedidos multilínea, mayores cantidades y peso/volumen,
   servicio largo, ventanas de recepción explícitas y skills de manipulación.
   Dos CDs, al menos un pedido cuya cobertura completa sea imposible; demostrar
   causa de stock versus flota/tiempo y costo de espera/servicio. Sin muelles,
   pallets, entregas divididas ni reglas derivadas de la etiqueta.
2. **B2C sintético:** cantidades pequeñas, ventanas diferentes, puntos compartidos,
   servicio corto y flota heterogénea; prioridad explícita y un caso de skills.
   Evidencia de secuencia, ventana de **inicio** de servicio, decisiones y costo.
3. Misma política, dataset OSRM, semilla y parámetros registrados para comparar
   greedy-v1/alternatives-v2; cada caso en su propio escenario, sin compartir
   reservas. Ninguno declara óptimo global. La comparación completa es 3.2.

Las cuatro demos de 2.4 y su evaluación se conservan. La regresión original
ORD-003 conserva 20 unidades SKU-C frente a 5 disponibles por CD y su excepción
STOCK_NO_FULL_COVERAGE. Casos B2B/B2C aún no creados; diagnósticos concluyentes
PROVEN versus hipótesis INFERRED y evidencia contextual corresponden a 3.1c.

## Evidencia previa y verificación proporcional

La primera revisión de 3.1a, **antes de añadir los límites**, se ejecutó el
2026-10-02 en Docker Desktop/Linux,
Python 3.14.7 y dependencias de requirements-dev.lock.txt instaladas solo en un
contenedor temporal de pruebas. Se montó backend en /app; las bases de
migración/integración son descartables y los casos HTTP crean datos aislados.
Dentro de Compose se usaron http://osrm:5000 y http://vroom:3000, mientras que
desde Windows corresponden las publicaciones 127.0.0.1. No se imprimieron URLs
con contraseñas ni se modificaron reservas de escenarios históricos.

| Comprobación anterior | Comando / evidencia | Resultado anterior |
|---|---|---|
| Suite integral backend, contratos, concurrencia y HTTP | `ROUTEOPS_TEST_DATABASE_URL=$ROUTEOPS_DATABASE_URL`, OSRM/VROOM de Compose y `ROUTEOPS_INTEGRATION_BASE_URL=http://backend:8000`; `python -m pytest -q -p no:cacheprovider` | **187 aprobadas**, sin skips, 114.24s |
| Ruff | `python -m ruff check src tests --no-cache` | Aprobado |
| mypy estricto | `python -m mypy src --cache-dir /tmp/mypy-m31` | Aprobado, 57 módulos |
| PostgreSQL/PostGIS/migraciones | Suite integral: upgrade/downgrade, objetos PostGIS, rollback, inmutabilidad, carreras y reservas; `alembic current` | Aprobado en la base anterior, head `c951e2a7d430` |
| Configuración Compose | `docker compose config --quiet` | Aprobado; límites y publicaciones locales conservados |
| CLI 2.1 | Tests existentes de templates, validación, errores controlados y round-trip CSV/XLSX en la suite | Aprobado con los encabezados anteriores |
| Cuatro demos reales | `test_isolated_demo_catalog_publishes_all_four_fixtures`, con OSRM/VROOM reales; políticas comparadas por tests de asignación y HTTP | Exclusivo CD-A/CD-B y excepción; elección elegible; alternatives-v2 rescata restringido; flota incompatible sin rutas |
| Smoke original por HTTP | Mismas verificaciones del script smoke: live/dependencies, POST BALANCED, geometría y excepciones; comprobación adicional exacta 2 rutas/4 pedidos/ORD-003 | Aprobado; run `eb660897-5ac9-40b6-b5f3-3c119f4cdb95` |
| Consulta del costo del smoke | GET estimated-operating-cost | `53118.7711`, operating-cost-v1; objetivo separado 4,766,044 unidades/escala 100 = 47,660.44 |
| Servicios disponibles | GET health/ready y frontend desde Windows | API ready; frontend HTTP 200 |
| Diff y archivos nuevos | `git diff --check`; revisión de los 25 archivos, UTF-8, whitespace, tamaños y patrones de secretos | Aprobado para la primera revisión |

El script smoke.ps1 no se pudo ejecutar como archivo: RemoteSigned lo rechaza
por falta de firma. No se cambió la política. Se ejecutaron sus comprobaciones
HTTP equivalentes con httpx desde el contenedor de pruebas, reforzadas con la
regresión exacta. La suite incluye además el smoke HTTP automatizado existente.
No se presenta el intento bloqueado de PowerShell como una ejecución aprobada.

La única advertencia es StarletteDeprecationWarning del uso de httpx en
TestClient, con las dependencias fijadas actuales; no afecta los resultados.
Las 53 capturas aceptadas de v0.2.1 son evidencia histórica; esta continuación
no es una revisión visual completa. Las cuatro demos y el smoke previos siguen
aplicando a la política de asignación y datos originales, que no se modificaron.

### Continuación con límites aprobados

Se ejecutaron las pruebas afectadas sobre el código nuevo. Los tests de
integración usan bases PostgreSQL/PostGIS descartables; no modifican escenarios
históricos. Se conservan activos triggers de inmutabilidad y reservas.

| Comprobación | Resultado |
|---|---|
| Unidad: CSV/XLSX, parser 2.1/2.2, encabezados antiguos, 0/negativo/fracción/overflow/borde, DTO, payload y conciliación | **104 aprobadas** en los cuatro módulos dirigidos |
| Integración: migración upgrade/downgrade, conservación PostGIS/triggers, publicación con procedencia, bloqueo de downgrade con límites publicados y replay | **8 aprobadas** en la selección dirigida |
| Replay histórico 2.1 | **1 integración adicional aprobada**: lote con encabezados anteriores, informe validado, publicación atómica y límites NULL |
| Integración: VROOM/OSRM reales, igualdad de distancia/tiempo, `max_tasks=1`, corrida 1.1 READY/HELD y error del solver con liberación independiente | **8 aprobadas** en la selección de solver/fallos; prueba end-to-end adicional incluida en la selección de 8 anterior |
| Integración: recuperación, competencia por stock, rollback de resultados incoherentes | Incluidas en la selección dirigida de **8 aprobadas** sobre fallos y concurrencia |
| Ruff y mypy estricto | Aprobados; mypy revisó 57 módulos |
| Frontend: contrato de lote 2.1 recuperable, TypeScript y build | **27 pruebas aprobadas**, TypeScript y Vite aprobados; warning de tamaño de bundle preexistente |
| HTTP Compose sobre servicios reconstruidos | **5 pruebas aprobadas**; API `/health/ready` devuelve ready, plantilla `vehicles` expone tres columnas opcionales |
| Costo histórico y Decimal | **17 pruebas dirigidas aprobadas**; objetivo VROOM y costo de negocio conservan campos distintos |
| Compose y disponibilidad | `docker compose config --quiet` aprobado; servicios locales reconstruidos, API ready, frontend HTTP 200 y Alembic `e3a1b7c9d240 (head)` |

Las selecciones de integración se solapan: no se suman sus cantidades como si
fueran pruebas distintas. Al cierre integral de 3.1 se repetirá la suite backend
completa, migraciones/integraciones, smoke, cuatro demos y frontend correspondiente,
junto con KPIs y diagnósticos 3.1b/c. Los límites nuevos se comprobaron ahora,
sin adjudicar a 3.1c una causa concluyente de pedidos no ruteados.

Inventario vigente de la candidatura: 30 archivos modificados y 8 nuevos.
Incluye parser/versiones, migración, publicación, pruebas y contrato del
frontend. El estado exacto se confirma con `git status --short` al entregar.

```text
M README.md
M backend/src/routeops/api/main.py
M backend/src/routeops/application/import_context.py
M backend/src/routeops/application/import_contract.py
M backend/src/routeops/application/import_templates.py
M backend/src/routeops/application/import_validation.py
M backend/src/routeops/application/planning.py
M backend/src/routeops/application/revision_problem.py
M backend/src/routeops/domain/optimization/contracts.py
M backend/src/routeops/infrastructure/persistence/import_publication.py
M backend/src/routeops/infrastructure/persistence/import_upload_repository.py
M backend/src/routeops/infrastructure/persistence/models.py
M backend/src/routeops/infrastructure/persistence/revision_runs.py
M backend/src/routeops/infrastructure/solver/vroom.py
M backend/tests/integration/test_compose_stack.py
M backend/tests/integration/test_persistence_migration.py
M backend/tests/unit/test_import_validation.py
M backend/tests/unit/test_planning_service.py
M backend/tests/unit/test_vroom_adapter.py
M docs/adr/0001-vroom-osrm.md
M docs/architecture.md
M docs/data-contracts.md
M docs/data-model.md
M docs/optimization-contract.md
M docs/product-requirements.md
M docs/research/toolchain-versions.md
M docs/roadmap.md
M frontend/src/ImportWorkspace.test.tsx
M frontend/src/ImportWorkspace.tsx
M frontend/src/import-api.ts
?? backend/migrations/versions/e3a1b7c9d240_add_vehicle_route_limits.py
?? backend/src/routeops/application/operating_cost.py
?? backend/src/routeops/application/optimization_reconciliation.py
?? backend/src/routeops/domain/optimization/validation.py
?? backend/src/routeops/infrastructure/persistence/operating_costs.py
?? backend/tests/unit/test_operating_cost.py
?? backend/tests/unit/test_optimization_contract_v1.py
?? docs/milestone-3-1a-contracts-costs.md
```

Git: HEAD sigue en 0241a5168d9f7c02a152cf3614bf502659ea33b3, rama común de 3.1
activa, índice vacío, sin commit/push. main/origin/main permanecen en la base;
v0.1.0→449e671822622919594fb5fa73e633cf32cf64c2,
v0.2.0→4a99f99708f9b4a31bf9d699e4b2ec91238c7021 y
v0.2.1→0241a5168d9f7c02a152cf3614bf502659ea33b3, intactas.

Se mantienen: exposición local, sin autenticación; ninguna garantía de óptimo
global; demanda importada puede superar límites de planificación; precisión
de ruta dependiente de OSRM y el extracto fijado; costos estimados sin peajes o
tráfico vivo. El mapper de revisión ya dependía directamente de modelos ORM y errores de
infraestructura en application; se registra ese límite arquitectónico sin
reorganizarlo fuera del alcance. La UI antigua sigue mostrando el proxy histórico; integrar el
nuevo catálogo corresponde a entregas posteriores.
