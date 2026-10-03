# Hito 3 — aceptación y cierre

Hito completo y formalmente aceptado por el usuario el 2026-10-03, con cierre
autorizado en `v0.3.0`. Rama de desarrollo `feat/m3-3-analytics-exports`, base
publicada de 3.2 `1fc356881b5bfa899399beafe3952da4f112f498`. La publicación y
retirada de las tres ramas integradas se verifican directamente en `origin`.

## Cobertura por modelo y método

| Área | B2B específico | B2C específico | Evidencia común reutilizada |
|---|---|---|---|
| Importación, validación, publicación | Integración HTTP `b2b-feasible` XLSX y `b2b-diagnostics` CSV. | Integración HTTP `b2c-task-pressure` XLSX y otros casos CSV; API adicional: XLSX factible y cinco CSV de presión, VALID → PUBLISHED, reintento con misma revisión. Consulta visual del XLSX publicado (44). | Contratos/contexto, incidencias e inmutabilidad; flujo visual aceptado en v0.2.1. No se afirma nueva carga B2B/B2C por interfaz. |
| Planificación, restricciones, diagnósticos | Integraciones de factible/diagnósticos; comparación y diagnósticos observados (02–06, 17–19, 33, 35). | Tres casos automatizados; seis pedidos factibles creados por UI (36–37); presión por API: dos ruteados/cuatro omitidos, diagnósticos INFERRED/PRIMARY y PROVEN/SUPPLEMENTARY observados (42). | Payload/reconciliación, límites previos a reserva, fallos y ORD-003; demos comunes no se etiquetan artificialmente B2B/B2C. |
| KPIs y comparación | Factible observado: dos pedidos, tarifas y diferencia temporal explícita. | Comparación b19fc98f creada/recuperada por UI: manual y ambas políticas con seis pedidos/cobertura 1 (40–41 intermedias, 45 final). | Fórmulas, dimensiones, denominadores nulos, procedencia y contexto congelado; objetivo/costo y alcance temporal. |
| Exportaciones | Descargas B2B CSV/XLSX por navegador, contrastadas con API por lectores independientes. | Descargas b19fc98f CSV/XLSX por navegador, todas las celdas contrastadas con API; integración exportación `b2c-task-pressure`. | Ceros/textos especiales, roles, metadatos históricos faltantes, fórmulas/enlaces inertes y fixture independiente. |
| Reservas y aceptación | Integración HTTP `b2b-feasible` aceptada; UI genérica 13–14 no se renombra B2B. | 64807885: seis HELD → CONFIRMED por UI, historial y recuperación del mismo ID (36, 38–39, registros DOM). | Confirmación, carrera aceptación/cancelación, reserva externa y disponibilidad entre revisiones. |
| Cancelación y recuperación | Integración HTTP `b2b-diagnostics` cancelada; UI genérica 15–16 y comparación B2B recuperada 35. | ab1317aa cancelada/reintentada por API, seis RELEASED observadas por UI (43); recarga/recuperación de corrida y comparación factibles por UI. | Concesiones, interrupciones, rollback, resultados tardíos y HTTP concurrente: automatización común, sin provocar fallos nuevos sobre historia. |

Se identificaron 24 pruebas pertinentes dentro del JUnit aprobado, sin
repetirlas. Las capacidades compartidas no dependen de etiquetas B2B/B2C.
No se revalidó visualmente cada combinación de modelo y formato.

## Escenarios adicionales aislados

- XLSX `b2c-feasible`: escenario `68e42a9b-863c-4101-bd40-87f4471a6899`,
  lote `669af227-3c5a-4294-a4a7-59861723c83d`, revisión 1
  `6e1955b4-c012-4511-b93b-f82fb908bbfa`.
- Corrida UI `64807885-bc85-427f-a80f-5f3da4bf2619`, ACCEPTED:
  seis pedidos/seis CONFIRMED. Recarga y «Recuperar solicitud» mantuvieron el
  ID; historial actualizado sin crear otra corrida.
- Comparación UI `b19fc98f-c6ca-44cb-98ef-07e72829c6ec`, READY:
  manual 10 940 m / 1 220 s conducción / 3 433 s espera / 1 726.2500 CLP;
  optimizados 4 024 m / 441 s / 0 s / 1 263.4500 CLP.
  Los tres cubren seis pedidos. La diferencia estimada incluye salida/espera;
  no acredita ahorro ejecutado ni optimalidad global.
- Cinco CSV `b2c-task-pressure`: escenario
  `bf8e37bb-9987-4b30-82a7-36bc354b2298`, lote
  `d503245e-e8db-4154-a7d8-e5df6334c9a2`, revisión 1.
  Corrida API `ab1317aa-a89f-4478-bfb8-d47f8687f4f2`, CANCELED:
  dos ruteados/cuatro omitidos/seis RELEASED. Cuatro ya se habían liberado al
  terminar el solver; cancelar liberó las dos restantes.

No se modificaron reservas anteriores. Los datos de revisión se conservan.

## Validación y evidencia

- JUnit final recuperado: **332 aprobadas**, 213 unitarias y 119 integraciones,
  cero errores/fallos/omitidas, 301.790 s. JUnit inicial 331 conservado.
- Stdout frontend final: **36 aprobadas / 10 archivos**, 9.31 s; build
  `tsc -b && vite build` aprobado. Ruff, mypy estricto (72 módulos), TypeScript,
  Compose y head Alembic `b82d6c4a910f`: evidencia previa documentada en 3.3.
- Los 20 archivos funcionales candidatos coinciden por SHA-256 con el inventario
  anterior. Esta continuación cambia documentación y dos imágenes seleccionadas;
  no repite suites completas.
- Se verificaron enlaces relativos, imágenes seleccionadas, diff, archivos
  nuevos, inventario SHA-256 y apertura de imágenes/exportaciones.

Evidencia local externa: `C:\Users\erosi\Desktop\routeops-visual-review-v0.3.0`.
ZIP al lado de esa carpeta: `routeops-visual-review-v0.3.0.zip`. Contiene informe,
manifest, hashes, 35 capturas anteriores y diez adicionales, exportaciones y
registros separados por navegador/API/pruebas/lector. Las capturas 40–41
conservan la transición del menú; 45 es la final estable. Solo dos capturas
sintéticas se seleccionaron para el README.

## Límites no bloqueantes y antecedentes de limpieza

Sin defecto funcional bloqueante detectado en los casos comprobados. Persisten
uso local sin autenticación/productivo, advertencias de bundle y Starlette/httpx,
acuse durable desconocido y cobertura no exhaustiva de dispositivos/Excel nativo.
No se extrapola rendimiento máximo.

La limpieza inicialmente recibió `rejected: blocked by policy`, sin motivo más
específico. No se eludió el rechazo: entonces se conservaron la carpeta provisional
y los dos contenedores detenidos, y se excluyó la carpeta de los candidatos.
Los registros siguientes describen esas etapas históricas; su estado final está
en la última sección. El [informe 3.3](milestone-3-3-analytics-exports.md) conserva
los antecedentes y sus limitaciones. Hito 4 no iniciado.

### Limpieza selectiva posterior — etapa parcial del 2026-10-03

Se compararon los 77 archivos provisionales con la carpeta final y el ZIP
revisado (118 archivos). Dos registros históricos únicos, `git-references.txt`
y `git-status-final.txt`, se trasladaron a `cleanup-audit/provisional-preserved`
y se verificaron por SHA-256. Los otros 75 ya estaban conservados exactamente.
El ZIP revisado anterior se conserva como `routeops-visual-review-v0.3.0-before-cleanup.zip`.

La eliminación autorizada de la carpeta provisional fue rechazada de nuevo
antes de ejecutarse: `rejected: blocked by policy`, sin detalle adicional.
No se intentó otro mecanismo. La carpeta permanece intacta y excluida de los
candidatos.

Se retiraron únicamente `routeops-m33-checks` y `routeops-m33-ui-checks`, detenidos,
tras comprobar sus IDs, comandos de espera para comprobaciones mediante exec,
montajes bind sin volúmenes y diferencia respecto de los nueve contenedores
operacionales. El JUnit final se leyó de nuevo del contenedor detenido y coincidió
con la copia conservada; el stdout frontend aprobado también permanece recuperado.
Se usó `docker rm` sin `--force` ni `--volumes`. No se eliminaron otros temporales,
dependencias, servicios, datos ni volúmenes. La limpieza queda parcial por la
carpeta bloqueada; su resultado actual está en `cleanup-audit/cleanup-result.json`.
No se repitieron suites funcionales por esta limpieza.

## Comprobación final del cierre autorizado — 2026-10-03

El usuario retiró manualmente `.visual-review-m33-temp`. Se comprobó su ausencia
en la raíz correcta; no se volvió a solicitar ni ejecutar su eliminación.
`routeops-m33-checks` y `routeops-m33-ui-checks` también están ausentes. No quedan
pendientes de limpieza de esta revisión. Los rechazos anteriores se conservan
como antecedentes, sin atribuir la eliminación manual a una acción de la herramienta.

La carpeta externa y el ZIP revisado se conservaron sin cambios: 128 archivos
coincidentes por bytes, 127 entradas del inventario SHA-256 verificadas (el propio
inventario no se incluye en sí mismo), 45 imágenes decodificadas correctamente.
SHA-256 del ZIP: `2a4b94d7a1cf3f7a2b0b7ca53369656e7dfe19f5d14b72221dacc2ac39fe6742`.
Los registros externos de limpieza parcial son históricos; esta sección registra
la comprobación final posterior a la intervención manual. No se crearon archivos
ni scripts externos durante el cierre.

Los 20 archivos funcionales coincidían con los hashes del inventario aprobado
al recuperar la entrega. El diff preparado detectó una línea vacía adicional
al final de `frontend/src/analytics-api.ts`; se retiró únicamente esa línea,
sin cambiar instrucciones, contratos ni comportamiento. Los otros 19 archivos
funcionales conservan exactamente sus hashes; el archivo ajustado coincide
con el aprobado al normalizar solo saltos finales de línea.
Se recuperaron y leyeron de nuevo JUnit final (332 aprobadas, cero fallos,
errores u omisiones) y stdout frontend (36 aprobadas, diez archivos, TypeScript
y build). No se repitieron suites: los ajustes finales son documentales y de
espacio en blanco. El diff preparado se comprueba nuevamente antes del commit.
Ruff, mypy, migraciones, Compose, smoke y demos reales siguen respaldados por
la evidencia aprobada; su método y procedencia se detallan en el informe 3.3.

Los nueve servicios existentes ya estaban activos, sin necesidad de recrearlos.
Los volúmenes `routeops_postgres-data` y `routeops_import-originals` se conservaron,
al igual que originales privados, datasets, archivos OSRM e historia local.
Las cinco páginas `/`, `/planning`, `/imports`, `/analytics` y `/comparisons`
respondieron HTTP 200; la API respondió HTTP 200 y `ready`. Esta comprobación es
de disponibilidad HTTP, no una nueva revisión visual ni una repetición del flujo.

El cierre usa un único commit `feat: complete RouteOps milestone 3`, integración
fast-forward y etiqueta anotada `v0.3.0` (`RouteOps milestone 3`). Las etiquetas
anteriores se conservan y únicamente se retiran las tres ramas del Hito 3 una vez
verificada su integración y publicación. No hay defectos bloqueantes pendientes
en el alcance aceptado; las limitaciones anteriores siguen vigentes. Hito 4 no iniciado.
