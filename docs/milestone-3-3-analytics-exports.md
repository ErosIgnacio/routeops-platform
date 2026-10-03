# Entrega 3.3 — interfaz, exportaciones y aceptación integral

Estado: **completa y aceptada con el Hito 3**, 2026-10-03; cierre autorizado
en `v0.3.0`. Hito 4 no iniciado.
Repositorio `C:\Users\erosi\Desktop\routeops-platform`, rama única
`feat/m3-3-analytics-exports`, base/cierre publicado de 3.2:
`1fc356881b5bfa899399beafe3952da4f112f498`. No hay nueva migración: head
`b82d6c4a910f`. Se preservaron datos históricos, etiquetas y ramas.

## Uso y alcance

La navegación enlaza tablero original, importaciones, planificación, analítica
y comparaciones. `/planning` conserva solicitud idempotente, recuperación,
aceptación/cancelación e historial; agrega el panel analítico de su corrida.

`/analytics` permite seleccionar escenario y recorrer el historial de corridas
en páginas de diez, o consultar explícitamente un UUID histórico. Recargar
conserva la corrida elegida; `?run=UUID` permite un enlace directo. La consulta
no acepta ni cancela: esas operaciones siguen en planificación. Seleccionar un
vehículo en el detalle enfoca su ruta; mostrar todas y centrar reutilizan el mapa.

Los valores/unidades/denominadores/versiones/procedencia vienen del backend.
No se calculan KPIs, costos o diferencias en React. Cero se muestra como cero;
`null` como no disponible con su motivo. Se separan objetivo VROOM y costo
operativo Decimal estimado. Las rutas canceladas son planes históricos estimados,
con estado actual de reservas visible, no entregas ejecutadas ni ahorro realizado.
Los tiempos muestran MEASURED, RECONSTRUCTED, PARTIAL y UNKNOWN, con intentos y
fases consultables. El total reconstruido acaba antes del COMMIT; el acuse final
no está medido. No incluye espera del usuario ni polling del navegador.

Diagnósticos: páginas de cinco, filtros exactos de etapa, certeza y código,
reinicio de página al cambiar el filtro, código/rol/alcance/detalle y evidencia
expandible. Los hechos y métricas operacionales del panel combinado/exportación
se leen en una única instantánea REPEATABLE READ; la página filtrada de diagnósticos
es una consulta separada y sus incidencias persistidas son inmutables.

`/comparisons`: elegir revisión publicada, añadir rutas manuales y escribir un
pedido por línea en el orden exacto. Vehículo y CD son explícitos; no se corrige
orden ni contenido silenciosamente. Pendientes, duplicados y desconocidos se
presentan antes de enviar; el backend evalúa la viabilidad y las infracciones.
La clave y entrada se guardan antes de enviar. Una respuesta perdida se recupera
con la misma clave/entrada; una identidad nueva requiere la acción explícita.
Historial de cinco elementos, selección y recarga no emiten acciones operacionales.

Manual, greedy-v1 y alternatives-v2 comparten contexto congelado y disponibilidad
inicial. Comparar no activa ni reserva inventario. La vista identifica el plan
mostrado y reemplaza sus geometrías, evitando mezclar alternativas en el mapa.
Cobertura/viabilidad anteceden a costos. No se elige automáticamente un ganador
por costo con menor cobertura o manual inviable, ni se declara optimalidad global.
Los metadatos ausentes de planes anteriores se identifican como no registrados;
no se inventan ni se reescriben resultados.

## Condición temporal comprobada

El manual conserva EFFECTIVE_SHIFT_START; los optimizados,
SOLVER_CHOSEN_WITHIN_EFFECTIVE_SHIFT. Es una condición del manual, no un requisito
común satisfecho igual por ambos. La entrada evaluada, cada ruta, API, interfaz
y exportación la conservan. Los instantes ISO incluyen offset y el contexto
identifica America/Santiago; no se equiparan horas UTC a horas locales.

Muestra nueva B2B en navegador, comparación
`6512953c-6093-4989-ab80-9ae5d72b2955`:

| Hecho central | Manual | alternatives-v2 |
|---|---:|---:|
| Pedidos / cobertura | 2 / 1 | 2 / 1 |
| Salida | 08:00:00−03:00 | 11:57:13+00:00 (08:57:13 local) |
| Conducción / servicio | 261 / 3000 s | 261 / 3000 s |
| Espera | 3433 s | 0 s |
| Duración operativa | 6694 s | 3261 s |
| Costo operativo estimado | 1301.5444 CLP | 1206.1833 CLP |

La diferencia de duración es −3433 s y de costo −95.3611 CLP. Son diferencias
descriptivas entre esos planes y sus condiciones de salida; no ahorro físico
realizado ni efecto atribuible exclusivamente al cambio de secuencia. Los pedidos
de esta muestra comparten coordenadas; el orden manual se conserva y VROOM escoge
otro orden válido. La prueba dirigida de 3.2 y sus seis casos aprobados se conservan.

## API nueva y consulta segura

| Método y recurso | Resultado |
|---|---|
| GET `/api/v1/scenarios/{id}/revisions/{no}/comparison-input` | IDs públicos y turno efectivo para el editor; sin originales |
| GET `/api/v1/runs/{id}/analytics` | Corrida, KPIs y todas las incidencias desde una instantánea |
| GET `/api/v1/runs/{id}/exports/{format}` | Exportación operacional de consulta |
| GET `/api/v1/comparisons/{id}/exports/{format}` | Contexto y resultado congelados verificados por su hash |

`format` es `csv` o `xlsx`. CSV entrega ZIP; XLSX entrega libro. Attachment con
nombre generado desde UUID, `Cache-Control: no-store`, `nosniff`. Formato inválido:
EXPORT_FORMAT_UNSUPPORTED (422); comparación sin resultado: EXPORT_NOT_READY
(409); exceso de celdas/texto XLSX: EXPORT_CELL_LIMIT (413); UUID/recurso inexistente
se rechaza mediante el contrato API existente. Corridas sin plan exportan su estado,
disponibilidad y hechos existentes, sin presentarlos como un resultado completo.
No se exponen originales privados, rutas físicas, tokens de propietario o credenciales.

## Contrato CSV/XLSX `analytics-export-v1`

Una tabla por CSV dentro del ZIP o por hoja XLSX; mismo orden de columnas y filas:

| Tabla | Contenido |
|---|---|
| summary | plan, KPI, valor, unidad, denominador, motivo de ausencia, versión y procedencia |
| routes | vehículo/CD, política e instante de salida, distancia, conducción, espera, servicio y duración |
| stops | secuencia, tipo, pedido/CD, llegada/inicio/fin de servicio, tiempos, carga posterior y coordenadas |
| diagnostics | pedido/código/etapa/certeza/rol/alcance/evidencia e infracciones manuales |
| utilization | dimensión, máximo y promedio ponderado, denominadores |
| differences | par, comparabilidad, alcance, deltas exactos, denominador y condición temporal |
| context | contexto completo por ruta JSON, tipo y valor |
| manual_input | vehículo, CD, posición, pedido y condición manual sin reparación |
| reservations | estado operacional actual por pedido; vacía en comparaciones |
| processing | tiempos/intentos o eventos de comparación; sin inventar duraciones |
| facts | documento API completo aplanado, incluyendo geometrías y procedencia |
| schema | versión y convenciones de interpretación |

CSV: UTF-8 con BOM, comas y escape RFC 4180. XLSX: OOXML con todas las celdas
como texto inlineStr; sin fórmulas, hyperlinks ni relaciones externas. IDs como
`00123` permanecen texto en XLSX. En CSV no existe tipado: un lector conserva
su representación lexical, pero Excel puede quitar ceros al abrir automáticamente;
debe importarse con las columnas de IDs como **Texto**. Para conservación tipada
sin configurar el lector, usar XLSX. No se añaden fórmulas `="00123"`.

`null` es `\N`; un texto que comienza con barra invertida duplica su primera
barra para distinguirlo del marcador. Decimales conservan sus cadenas exactas,
sin conversión a float, separador punto y unidades/moneda separadas. Fechas e
instantes conservan ISO 8601 y offsets del documento; zona IANA en contexto.
Arrays/objetos en columnas compuestas son JSON canónico. En `facts`, las rutas
JSON y el tipo distinguen null, cadenas, números y contenedores vacíos.

Texto CSV cuyo primer contenido interpretable empieza con `=`, `+`, `-`, `@`,
tab/CR/LF o esquema de enlace recibe apóstrofo inicial. Un apóstrofo original
también se escapa para distinguirlo. No usar un lector que quite estas protecciones
y luego evalúe contenido. Controles no permitidos por XML 1.0 se representan
visiblemente `\uXXXX`; CR permitido conserva su valor mediante referencia XML.
Todas las celdas XLSX son cadenas incluso cuando parecen fórmulas, URLs o números.

Límites: un millón de celdas agregadas antes de serializar, 32767 caracteres por
celda XLSX. No se relajaron límites de importación/solver ni triggers. La consulta
ensambla un documento acotado por los límites actuales de planificación; no es
un exportador streaming de capacidad máxima ni un benchmark de un millón de celdas.
ZIP tiene orden/fechas deterministas; para un documento idéntico los bytes se repiten.
Estados actuales de reservas pueden cambiar legítimamente entre dos consultas;
los resultados históricos y el contexto congelado permanecen inmutables.
La columna `role` conserva el rol registrado: PRIMARY/SUPPLEMENTARY en la corrida
diagnóstica. Las incidencias manuales y razones de los resultados congelados de
comparación que no registraron ese campo exportan `null` (`\N`), sin deducirlo de
severidad, posición o certeza. La política de salida también permanece
LEGACY_NOT_RECORDED cuando no consta en la corrida.

## Verificación integral ejecutada

Dependencias bloqueadas de backend/frontend en contenedores de comprobación
aislados, sobre fuentes actuales. Windows/WSL2 y red `routeops_default`.
URLs internas: OSRM `http://osrm:5000`, VROOM `http://vroom:3000`; Windows/
navegador usan publicaciones locales 127.0.0.1. VROOM solo mediante SolverGateway.

| Comprobación | Resultado final |
|---|---|
| Backend completo, sin omitir tests | **332 passed**: 213 unitarias + 119 integraciones; 0 fallos/omisiones; JUnit 301.790 s (salida pytest 301.81 s) |
| PostgreSQL/PostGIS, HTTP y migraciones | Incluidos en las 119 integraciones; head b82d6c4a910f, downgrade descartable conserva PostGIS/historia |
| Ruff / mypy estricto | Aprobados; 72 módulos de fuente |
| Frontend | **36 passed**, 10 archivos; 28 existentes + 8 nuevas de analítica/comparación |
| TypeScript / Vite | Aprobados; advertencia de chunk >500 kB, no bloqueante |
| Compose / diff | config --quiet y diff --check aprobados |
| Smoke original y cuatro demos reales | Incluidos en HTTP integral con OSRM/VROOM y ORD-003 intacto |
| B2B/B2C y comparación real | Casos contractuales, costos, KPIs, diagnósticos, límites y ambas políticas aprobados |
| Consulta/exportación inmutable | Cinco integraciones nuevas, doce unitarias; sin alterar inventario/reservas/resultados |
| Lector independiente | openpyxl 3.1.5, 12 hojas, igualdad celda por celda con API; CSV contrastado con csv/zipfile |

Comando integral dentro del contenedor de pruebas, con database URL privada
tomada del entorno (nunca registrada en informes):

```sh
export ROUTEOPS_TEST_DATABASE_URL="$ROUTEOPS_DATABASE_URL"
ROUTEOPS_TEST_OSRM_URL=http://osrm:5000 ROUTEOPS_TEST_VROOM_URL=http://vroom:3000 \
python -m pytest -q -p no:cacheprovider --junitxml=/tmp/m33-full.xml --tb=short
python -m ruff check .
python -m mypy --strict src
npm test -- --run
npx tsc --noEmit
npm run build
docker compose config --quiet
docker compose exec -T backend alembic current
git diff --check
```

Desarrollo: primer test del XLSX detectó normalización de CR; se corrigió y sus
once unitarias aprobaron. Se reforzó REPEATABLE READ antes de la suite integral:
una cancelación real entre lecturas no mezcla estados ni reservas; la consulta
siguiente sí ve CANCELED/RELEASED. El primer TypeScript detectó inferencia recursiva
en el paginado de escenarios; se explicitó el tipo y aprobó. Resultados iniciales
no se presentan como aprobados. La primera suite integral aprobó 331 pruebas
(212 unitarias y 119 integraciones, 222.81 s). La revisión posterior encontró
dos diferencias en la exportación: una política de salida atribuida sin metadato
persistido y la falta de una columna explícita para el rol del diagnóstico.
Se corrigieron, se añadió la prueba dirigida y la suite final aprobó 332 pruebas.
Los ejemplos se regeneraron con esa corrección y se abrieron con el lector
independiente. Frontend/TypeScript/build se repitieron tras los ajustes visuales.

## Evidencia real de navegador y separación de métodos

Navegador integrado mediante `mcp__cua_repl`, sin control nativo del escritorio.
Escritorio 1280×900 y móvil 390×844. Capturas reales y archivos verificados fuera
del repositorio: `C:\Users\erosi\Desktop\routeops-visual-review-v0.3.0`.
Informe, manifest, JUnit, documentos API sintéticos, ejemplos ZIP/XLSX y SHA-256
en esa carpeta; evidencias v0.2.0/v0.2.1 conservadas.

| Caso | Observado mediante interfaz | Evidencia |
|---|---|---|
| Tablero original | Dos rutas, cuatro entregas, excepción ORD-003 y colores establecidos | 28–29 |
| B2B factible | Creación, READY, mismo UUID tras recarga/recuperación, manual y alternativas, secuencia/horarios | 02, 04–06 |
| Móvil | Mapas y lectura sin desbordamiento de página; tablas con scroll propio | 22, 30–31 |
| Manual inviable | MANUAL_STOCK_NO_FULL_COVERAGE, PROVEN, entrada intacta y falta SKU-X=5 | 09 |
| Coberturas distintas | Greedy 0.5; alternatives 1 y dos CDs; no ahorro comparable automático | 10–12 |
| Ciclo de revisión | READY/HELD, recarga y recuperación; ACCEPTED/CONFIRMED; otra corrida CANCELED/RELEASED, historial sin recarga | 13–16 |
| Diagnósticos | 10 incidencias, segunda página desde 6; INFERRED filtra a una y vuelve a página 1 | 17–19 |
| Tiempos y utilización | Máximo/promedio por unidades/peso/volumen, total PARTIAL y acuse UNKNOWN separados | 21, 32 |
| Cámara | Zoom manual asentado conservado al actualizar mismo resultado; centrar/selección y adaptación del mapa | 26–27 |
| Exportación | Enlaces CSV y XLSX descargan archivos reales; abiertos y contrastados independientemente | 35, browser-downloads.json, independent-reader.json y ejemplos |
| Navegación de importaciones | Enlaces nuevos visibles en la pantalla existente; no constituye una repetición visual del flujo de carga/publicación | 34 |

Los tres escenarios B2B/stock compartido/diagnósticos se prepararon por API; las
dos comparaciones se crearon y recuperaron en la UI. La corrida diagnóstica se
solicitó por API y sus KPIs/paginación/filtros se revisaron en UI. Los dos escenarios
de aceptación/cancelación y sus solicitudes/transiciones se crearon por UI.
Las cuatro demos, B2C, concurrencia/rollback/fencing/recuperación/integridad de
reservas y migraciones se verificaron por pruebas backend/HTTP; no se declaran
como nuevas demostraciones visuales de cada fallo inyectado.

El navegador inicialmente no registró el evento de descarga del botón con Blob;
se sustituyó por enlaces attachment estables. El mecanismo downloadMedia sobre
los enlaces visibles guardó XLSX y ZIP reales en Downloads; se copiaron a evidencia.
La primera vista móvil mostró 432 px de documento para viewport 390 por hashes
largos: texto envuelto, resultado 375 px útiles con scrollbar de 15 px, sin
desbordamiento. Se corrigió la legibilidad de filtros/denominador/clasificación.
Capturas iniciales/animación se conservan como registro, no como evidencia final
de cámara estable. No se desplazaron puntos ni inventaron conexiones de carretera.

Escenarios/resultados nuevos identificados en manifest: B2B dcc5365b…,
compartido 7b8f459c…, diagnóstico ed4c1ab2…, revisión/aceptación 7b98d2f8…,
cancelación ef2bab62…. Comparaciones 6512953c… y 9fb316fa…. Corridas 74e83e2f…
ACCEPTED, 33e552b4… CANCELED y 148192c6… READY. La reserva B2B-OK de esta última
permanece HELD para revisión; B2B-SERVICE está RELEASED al no rutearse. No se
liberaron reservas de escenarios anteriores.

## Límites no bloqueantes

- Sin óptimo global, ganador automático, baselines históricos sin contexto,
  tiempos manuales declarados ni subconjuntos comparados como plan completo.
- Sin autenticación/red externa ni descarga de originales. Uso local confiable.
- Los planes siguen estimados: no hay telemetría de entregas ejecutadas.
- Acuse COMMIT/HTTP no medido; intervalos faltantes UNKNOWN y subtotal PARTIAL.
- No benchmark adicional de exportación máxima; conservados límites y muestras
  de 2.6/3.1/3.2. No extrapolación de importación al solver.
- CSV carece de tipos: importar IDs como texto; XLSX es la opción tipada segura.
- Las hojas se abrieron con openpyxl; no se verificó esta entrega en Excel nativo,
  Safari ni todos los tamaños/dispositivos. Recursos cartográficos dependen de red.
- Bundle frontend ~1.9 MB minificado/~530 kB gzip y advertencia Starlette/httpx
  conocidas, no bloqueantes; dividir chunks corresponde a trabajo posterior.

No defecto bloqueante pendiente identificado en los casos comprobados. El usuario
aceptó la implementación y evidencia y autorizó commit, integración y etiqueta
del Hito 3. La [comprobación de cierre](milestone-3-final-review.md) registra el
estado final; las secciones siguientes conservan las etapas históricas anteriores.

## Reanudación y consolidación final de evidencia

Se recuperó `/tmp/m33-full.xml` de `routeops-m33-checks` como
`backend-junit-final.xml`: 332 pruebas, 0 errores/fallos/omisiones,
301.790 s, timestamp 2026-10-02T17:11:48.892267+00:00. El JUnit inicial de
331 pruebas se conserva como `backend-junit.xml`; no se confunden sus resultados.
`frontend-final.txt` conserva la salida de 36 pruebas y el build `tsc -b && vite
build`. El `tsc --noEmit` separado y la advertencia única de backend constan en
el registro previo de ejecución, sin un stdout independiente recuperado.
El JUnit no almacena el texto completo de esa advertencia. No se repitieron las
suites completas: esta continuación solo ajustó documentación y evidencias.
Ruff, mypy estricto sobre 72 módulos, Compose y head Alembic se confirmaron de
nuevo. La salida y su origen constan en `continuation-checks.json`.

El rechazo anterior decía: “Automatic approval review failed: You've hit your
usage limit” y “The action was not executed because automatic approval review
could not be completed. This is a review failure, not a determination that the
action is unsafe.” Es un mensaje del registro de herramienta anterior, no un
log original recuperado del revisor. No hay motivo adicional disponible.
La recuperación y consolidación autorizadas pudieron ejecutarse en esta sesión,
sin cambiar políticas ni eludir un bloqueo.

`consolidated-reader-verification.json` registra la apertura/decodificación
completa de las 35 imágenes conservadas, los cinco XLSX y los cuatro ZIP con
CSV. Los cuatro pares de exportación corresponden a dos comparaciones, una
corrida cancelada y la corrida diagnóstica. Esta última pareja se añadió por API
para comprobar los diez roles persistidos; no es una descarga observada en UI.
El quinto XLSX es el fixture adversarial de identificadores. Las celdas se
contrastaron con los documentos API guardados y los recursos actuales por GET.
Se comprobaron fórmulas/enlaces inertes, ceros iniciales, CR, acentos y null,
sin reescribir los ejemplos existentes. No se crearon escenarios ni se cambiaron
reservas en esta continuación. Los estados ACCEPTED/CONFIRMED, CANCELED/RELEASED
y READY/HELD+RELEASED se confirmaron de nuevo exclusivamente por GET.

La auditoría auxiliar se corrigió para no exigir un rol PRIMARY inexistente en
las comparaciones y para quitar secuencias ANSI solo al interpretar el stdout
frontend. Esas aserciones eran defectos del verificador provisional, no fallos
de RouteOps; los archivos originales se conservaron.

La carpeta externa contiene `report.md`, `manifest.json`, `SHA256SUMS.txt`,
`pending-files.json`, `git-references.txt`, los informes y toda la evidencia.
El manifest inicial se conserva como `manifest-initial.json`; no se usa su lista
pendiente obsoleta como resultado final. El inventario distingue capturas de
desarrollo y finales, métodos navegador/API/pruebas/lector, IDs y procedencia.
La copia se verifica por SHA-256 y apertura antes de retirar exclusivamente
`.visual-review-m33-temp` y los dos contenedores propios de comprobación.
No se borran volúmenes, originales, servicios ni registros históricos.

La copia final se completó y verificó. La herramienta bloqueó antes de ejecutar
el comando combinado de comprobación/eliminación de la carpeta provisional:
`rejected: blocked by policy`. No proporcionó un motivo más específico. La llamada
posterior para retirar los dos contenedores no llegó a ejecutarse. La limpieza
queda pendiente: se conservan `.visual-review-m33-temp`, `routeops-m33-checks`
y `routeops-m33-ui-checks`. No se intentó otro mecanismo de eliminación ni se
cambiaron políticas. `cleanup-block.json` registra el bloqueo; el manifest final
incluye este pendiente y la evidencia permanece disponible en ambos destinos.

La captura 34 acredita navegación de importaciones, no una nueva carga CSV/XLSX.
En esa vista el texto «Crear» se parte en dos líneas en el botón estrecho:
observación cosmética no bloqueante, sin reparación en esta consolidación.
Las capturas 14–15 acreditan reservas y planes históricos, pero su estilo de
filtros fue reemplazado por las vistas finales 32–33. La evidencia de cámara
26–27 compara posiciones relativas al mapa; cambió el scroll de la página,
por lo que no se afirma igualdad píxel por píxel del viewport entero.

## Preparación de la revisión final del Hito 3

La [matriz B2B/B2C y revisión final](milestone-3-final-review.md) distingue
evidencia específica, automatización compartida y observaciones del navegador.
Se añadieron dos escenarios B2C aislados, una corrida aceptada por UI, otra
cancelada por API y una comparación manual/optimizada por UI. CSV/XLSX se
importaron, validaron y publicaron por API; la consulta del XLSX publicado y su
reintento también se observaron en la interfaz. No se presenta esa preparación
como una carga visual. Diez capturas nuevas complementan las 35 anteriores;
las dos exportaciones descargadas por navegador se contrastaron con API usando
openpyxl/csv. Los 20 archivos funcionales coinciden por SHA-256 con el inventario
previo; no se repitieron suites completas.

El README presenta problema, capacidades, casos, stack, inicio/recorrido,
documentación y límites. Dos capturas sintéticas fueron seleccionadas para
versionar; informes extensos y demás evidencia quedan fuera. El ZIP conserva
la carpeta de evidencia y su inventario final verificable.
La limpieza `blocked by policy` no se reintentó; los dos contenedores propios
siguen presentes y detenidos y la carpeta provisional permanece excluida
localmente de los candidatos. No se publica ni acepta todavía `v0.3.0`.

### Limpieza selectiva posterior — 2026-10-03

La nueva solicitud de limpieza comparó los 77 archivos provisionales y verificó
el ZIP final revisado. Dos registros históricos únicos de Git se conservaron en
`cleanup-audit/provisional-preserved`; los otros 75 ya estaban copiados exactamente.
Se preservó el ZIP previo a la actualización del inventario. La herramienta
volvió a rechazar antes de ejecutar la eliminación de `.visual-review-m33-temp`:
`rejected: blocked by policy`. No se intentó eludirlo y la carpeta sigue intacta.

La retirada separada de los dos contenedores descartables sí se ejecutó mediante
`docker rm routeops-m33-checks routeops-m33-ui-checks`, sin fuerza ni eliminación
de volúmenes. Sus IDs se comprobaron frente a los nueve servicios operacionales
y el JUnit final coincidió por bytes con el recuperado del contenedor detenido.
Se conservaron informes, originales, datasets, servicios, volúmenes y dependencias.
El resultado actual sustituye únicamente el estado pendiente de esos contenedores;
los rechazos históricos se conservan como evidencia. Persisten revisión final y
cierre autorizado pendientes, sin repetir suites funcionales ni iniciar Hito 4.

### Cierre tras intervención manual — 2026-10-03

El usuario eliminó manualmente la carpeta provisional. Se verificó su ausencia
y la de ambos contenedores de comprobación; no se reintentó una eliminación
bloqueada. Se conservó íntegro el paquete externo revisado: 128 archivos en
carpeta y ZIP, 45 capturas legibles, inventario SHA-256 comprobado y exportaciones
previamente contrastadas mediante lectores independientes. Los registros externos
del bloqueo y limpieza parcial permanecen históricos, sin sobrescribir la evidencia.

Los 20 archivos funcionales coincidían con sus hashes aprobados al recuperar
la entrega: JUnit final 332/332 y frontend 36/36 se leyeron nuevamente sin repetir
suites. El diff preparado detectó y permitió retirar una línea vacía final en
`frontend/src/analytics-api.ts`; no cambió su comportamiento. Los otros ajustes
de este cierre registran aceptación y estado actual de la documentación.
Se comprobaron los nueve servicios activos, ambos volúmenes conservados, HTTP 200
en las cinco páginas y API `ready`; no se atribuye a estos GET una revisión visual.
El [informe final](milestone-3-final-review.md) conserva la matriz B2B/B2C,
resultados, límites y procedencia de cada método. La entrega se cierra con un
único commit, fast-forward y etiqueta anotada `v0.3.0`, sin mover etiquetas previas
ni iniciar Hito 4.
