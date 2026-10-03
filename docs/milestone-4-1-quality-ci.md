# Entrega 4.1 — calidad automatizada y CI reproducible

Implementación para revisión en `feat/m4-1-quality-ci`, desde Hito 3
`c308bfb5224edf8268102cafd813faa8e9d5f206`. Sin integración en main ni etiqueta.
Los commits de avance publicados no implican aceptación: el resultado efectivo
de cada commit y sus informes están en [GitHub Actions](https://github.com/ErosIgnacio/routeops-platform/actions/workflows/quality.yml).
El informe de entrega identifica la ejecución y SHA comprobados. 4.2/4.3 no iniciadas.

## Trabajos y disparadores

Push en main, feat/** y fix/**; pull requests a main; ejecución manual.
No se usa `pull_request_target`, secretos del repositorio, despliegue ni escritura
desde CI. `contents: read` es el único permiso; checkout no conserva credenciales.
Las acciones están fijadas por SHA completo, no por etiquetas móviles.
Una nueva ejecución de la misma referencia cancela la anterior.

| Trabajo | Comprobaciones | Dependencia |
|---|---|---|
| Backend | Python 3.14.7, locks, Ruff format/check en src/tests/migrations y herramientas CI; mypy estricto; unitarias; contratos CI; cobertura informativa | Ninguna |
| Frontend | Node 24.21.0, npm ci --ignore-scripts, Vitest, TypeScript y build Vite | Ninguna |
| Integración | Compose, extract/car/partition/customize, nueve servicios, migraciones e integraciones completas, cinco páginas HTTP 200 y smoke original | Backend y frontend aprobados |

Ubuntu 24.04 aloja los trabajos. La imagen del runner y servicios de descarga
pueden evolucionar; no se afirma reproducción bit a bit de toda la infraestructura.
Versiones de aplicación, locks, acciones, imágenes OSRM/VROOM/PostGIS y mapa
quedan explícitos. Python y Node conservan las versiones existentes de Docker.
No se sustituyen perfiles, versiones de solver ni datos por aproximaciones.

JUnit debe tener pruebas y cero fallos, errores u omisiones; una integración
deshabilitada por configuración hace fallar este control. No hay umbral arbitrario
de cobertura. Se conserva cobertura XML de unitarias para inspección, sin
presentarla como cobertura de las integraciones. Deseleccionar el otro grupo
mediante marcadores no cuenta como omitir una prueba seleccionada.

## Fuente OSM fijada y procedencia

El descargador Overpass de desarrollo sigue intacto. Una descarga actual no
garantiza los bytes del extracto aprobado: por eso CI restaura una fixture de
fuente pública comprimida, `infrastructure/ci/santiago-demo.osm.gz`.
Es una excepción deliberada para distribuir la fuente reproducible de CI,
no un índice OSRM generado ni una caché de una máquina particular.
Sus 4 145 215 bytes descomprimidos deben coincidir con el lock original:
`22230c1c093a0fe50bbb240cce3d2c36aaf34e8bfb2fe136b76405084de213c4`.

`osm-archive.json` fija tamaño y SHA del gzip; el restaurador comprueba ambos
antes de expandir, limita bytes expandidos y verifica tamaño/SHA original antes
de publicar el archivo. Rechaza sobrescribir una fuente diferente y elimina
solamente su salida parcial. Los metadatos ODbL, atribución y query originales
permanecen en `data/osrm/source-lock.json` y las [notificaciones](../THIRD_PARTY_NOTICES.md).
Se ejecuta el mismo `prepare-osrm.sh` (car.lua, MLD extract/partition/customize)
con la misma imagen por digest. CI no descarga un mapa cambiante ni usa los
índices locales de `data/osrm`.

## Aislamiento y limpieza

`scripts/ci/integration.sh` genera un proyecto `routeops-ci-<uuid>` y configuración
de uso exclusivo en `.ci-work/ci.env`, con contraseña aleatoria efímera. No lee
el .env local. Los endpoints internos son database/osrm/vroom/backend/frontend;
la API y frontend se publican únicamente en loopback con puertos asignados.
Los demás puertos operacionales se eliminan mediante `!override` en el overlay.
Compose >= 2.24.4 es requerido; se conservan sus restricciones e imágenes base.

El mapa de CI y sus índices se generan en `.ci-work/osrm`. Los volúmenes y red
pertenecen exclusivamente al proyecto nuevo. Las fixtures PostgreSQL crean
bases UUID y retiran solo esas bases al terminar; las pruebas HTTP usan la base
descartable del stack, jamás la operacional. Los originales privados y todos
los datos locales quedan fuera del proyecto de comprobación.

El trap captura diagnóstico, oculta la contraseña en consola y archivos de
informe, retira solo ese proyecto con sus volúmenes y borra su env efímero.
Un fallo de retirada falla la ejecución. No hay prune global, limpieza de
volúmenes ajenos ni modificación de escenarios anteriores. Si el proceso
recibe SIGKILL no puede ejecutar trap: el runner alojado se descarta; localmente
se debe identificar el proyecto concreto por etiquetas y retirarlo explícitamente.
No ejecutar esta comprobación dos veces simultáneamente en el mismo checkout:
usa directorios de trabajo locales compartidos, aunque los proyectos tengan UUID.

Artefactos durante 14 días: JUnit unitario/frontend/integración, cobertura
unitaria, logs Compose saneados, servicios y smoke. No se suben .env, fuentes
originales de importación, volúmenes, índices OSRM ni todo el árbol de trabajo.
La carpeta de informes es oculta: `include-hidden-files` se habilita únicamente
con patrones explícitos XML/TXT revisados, nunca para todo el checkout.
La imagen de comprobación tiene su propio allowlist Dockerfile.ci.dockerignore;
incluye las pruebas y excluye originales privados y cachés del contexto de build.
Los pasos estáticos, instalaciones y contratos CI también quedan en el log del
trabajo. Las excepciones reproducidas usan únicamente datos sintéticos.

## Cobertura crítica reutilizada

| Criterio | Evidencia automatizada existente |
|---|---|
| Contratos y compatibilidad | test_import_validation, test_import_context, test_optimization_contract_v1; CSV antiguo sin límites opcionales y versiones/hashes históricos |
| Migraciones e inmutabilidad | test_persistence_migration: upgrade/downgrade, restricciones, snapshot/procedencia, no modificación histórica |
| Stock y reservas | Asignación completa; competencia concurrente, rollback, reservas externas, revisión nueva, aceptación/cancelación repetidas |
| Recuperación | Concesiones, fencing de dueño, resultados tardíos, reintentos y estados; validación y planificación |
| Comparación | test_plan_comparisons: contexto congelado, manual exacto, temporalidad, políticas, idempotencia y stock inalterado |
| Diagnósticos | test_planning_diagnostics y test_diagnostics: certeza/rol/evidencia y cinco casos B2B/B2C |
| Exportaciones | test_analytics_exports y test_analytics_export: igualdad API, snapshot coherente durante cancelación, datos especiales e historia sin campos inventados |
| Flujos HTTP reales | test_compose_stack y test_m26_http_acceptance: importación, publicación, demos, recuperación y coherencia de reservas/rutas |

B2B/B2C conservan fixtures `b2b-feasible`, `b2b-diagnostics`, `b2c-feasible`,
`b2c-task-pressure` y `b2c-distance-inferred`. Integraciones comprueban esos
casos mediante SolverGateway real; no se renombran pruebas comunes como
observaciones visuales específicas. En exportación se reutilizan comparación
B2B factible y presión B2C, verificando métricas, restricciones y stock intacto.
No se añade una suite duplicada ni porcentaje arbitrario para estos criterios.
Cinco pruebas nuevas protegen checksum/expansión/salida parcial, no sobrescritura,
restauración repetida y el rechazo de JUnit vacío o con omisiones/fallos.

## Comprobación local y estado de evidencia

Comprobado durante 4.1 en Linux/Python 3.14.7: 213 unitarias aprobadas,
cinco contratos CI, Ruff y mypy estricto sobre 72 módulos. La incorporación
del gate de formato requirió reformatear 25 archivos Python existentes; se
contrastó su AST antes/después con UTF-8 explícito, sin cambios ejecutables.
No hay migración nueva, cambio de política de asignación ni contrato del solver.
Los resultados finales de integración, frontend y smoke para el commit entregado
se consultan en la ejecución real y sus JUnit, sin sustituirlos por los del Hito 3.

Primera ejecución real: [37148771237](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37148771237),
commit `d341514b65d864d0bd6d19e986a196d833e7d9d9`. Backend y frontend aprobados;
integración falló antes de ejecutar pruebas porque el .dockerignore de producción
excluía tests. No se presenta como integración aprobada. La corrección agrega
un contexto específico de comprobación. También se corrigió la exclusión de
informes ocultos en upload-artifact y se fijó v6/Node24 por SHA, conforme a su
documentación. El trap retiró sus nueve servicios, red y dos volúmenes efímeros.

En Linux, desde checkout limpio, con Docker/Compose, Python, Bash, curl y jq:

```bash
python -m pip install -r backend/requirements-dev.lock.txt
cd backend
ruff format --check src tests migrations ../scripts/ci
ruff check src tests migrations ../scripts/ci
mypy src
pytest -m 'not integration'
cd ..
python -m unittest discover -s scripts/ci -p 'test_*.py' -v
cd frontend
npm ci --ignore-scripts
npm test
npx tsc --noEmit
npm run build
cd ..
bash scripts/ci/integration.sh
```

En Windows, las comprobaciones Python/Node existentes siguen disponibles.
Para el orquestador Bash usar Linux/WSL2 con integración Docker habilitada;
no traducir el script de limpieza a una combinación destructiva de shells.
No utilizar ROUTEOPS_TEST_DATABASE_URL operacional para comprobar esta CI.

## Límites y fuentes oficiales

No se incorpora despliegue, CI de navegadores reales, autenticación, una auditoría
de seguridad ni benchmarks nuevos de capacidad; pertenecen a entregas posteriores.
Las capturas aceptadas del Hito 3 permanecen como evidencia visual separada.
Persisten las advertencias conocidas del bundle y Starlette/httpx; no se ocultan
fallos ni se modifican expectativas de pruebas para lograr una ejecución verde.

Fuentes revisadas: [checkout v6](https://github.com/actions/checkout/blob/v6/README.md),
[setup-python v6](https://github.com/actions/setup-python/blob/v6/README.md),
[setup-node v6](https://github.com/actions/setup-node/blob/v6/README.md),
[upload-artifact v6](https://github.com/actions/upload-artifact/blob/v6/README.md),
[permisos y fijación de acciones](https://docs.github.com/en/actions/reference/security/secure-use),
[merge/override Compose](https://docs.docker.com/reference/compose-file/merge/).
Las actualizaciones futuras deben revisar release/notas/capacidades y sustituir
los SHA intencionalmente; no seguir una etiqueta móvil en el workflow.
