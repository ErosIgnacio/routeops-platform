# Entrega 4.1 — calidad automatizada y CI reproducible

Aceptación técnica formal del usuario el **2026-10-04** sobre el commit funcional
`f7582f0dc9f936fe2b93da291df9d47016dd01d9`, en `feat/m4-1-quality-ci`, desde Hito 3
`c308bfb5224edf8268102cafd813faa8e9d5f206`. Cierre documental y publicación por
fast-forward autorizados, conservando la rama y todas las etiquetas.
La limpieza local pendiente registrada abajo no condiciona este cierre.
Los resultados e informes están en [GitHub Actions](https://github.com/ErosIgnacio/routeops-platform/actions/workflows/quality.yml).
4.2 se inicia después de verificar la publicación; 4.3 permanece pendiente.

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
Solo el frontend de CI permite además el hostname interno `frontend` mediante
`__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS`; no se deshabilita allowedHosts ni se
modifica la configuración operacional de Vite.

El mapa de CI y sus índices se generan en `.ci-work/osrm`. Los volúmenes y red
pertenecen exclusivamente al proyecto nuevo. Las fixtures PostgreSQL crean
bases UUID y retiran solo esas bases al terminar; las pruebas HTTP usan la base
descartable del stack, jamás la operacional. Los originales privados y todos
los datos locales quedan fuera del proyecto de comprobación.

El trap captura diagnóstico, oculta la contraseña en consola y archivos de
informe, retira solo ese proyecto con sus volúmenes y borra su env efímero.
El saneamiento reemplaza archivos atómicamente para admitir informes de otro
UID sin ampliar permisos. Solo deja `sanitized.ok` si termina; la subida de
integración exige esa señal y no publica informes sin sanear tras un fallo.
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
Siete pruebas nuevas protegen checksum/expansión/salida parcial, no sobrescritura,
restauración repetida, rechazo de JUnit vacío/con omisiones/fallos y saneamiento
atómico de informes de solo lectura sin dejar una señal de subida tras error.

## Comprobación local y estado de evidencia

Comprobado durante 4.1 en Linux/Python 3.14.7: 213 unitarias aprobadas,
siete contratos CI, Ruff y mypy estricto sobre 72 módulos. La incorporación
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

Segunda ejecución: [37149428055](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37149428055),
commit `afafc2b03f0280103f4ee56b373dbe792ddd93b0`. Backend/frontend aprobados,
artefactos descargados y contrastados con sus SHA oficiales: 213 unitarias y
36 frontend, cero fallos/errores/omisiones. El contexto Docker corregido recoge
las 332 pruebas. Integración no llegó a ejecutarlas: Vite devolvió HTTP 403 al
hostname interno frontend. Se agrega únicamente ese hostname en el overlay CI,
conforme al mecanismo documentado; todos sus recursos descartables se retiraron.

Tercera ejecución: [37149988881](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37149988881),
commit `61a3a8ec905ef9f587e87331c7d289fcc0d4ac03`. Las 119 integraciones aprobaron,
sin fallos/omisiones, en 198.028 s; backend/frontend también. Las cinco páginas
respondieron HTTP 200 y la API ready; se produjo smoke.txt. El workflow falló
después por PermissionError al reescribir el JUnit de otro UID durante saneamiento;
no se considera ejecución integral aprobada. Se corrige con reemplazo atómico
y subida condicionada al saneamiento completo, sin chmod/chown ni reducción
de expectativas. Los recursos del proyecto efímero se retiraron igualmente.

### Resultado integral comprobado

[Ejecución 37150811990](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37150811990)
aprobada sobre `f7582f0dc9f936fe2b93da291df9d47016dd01d9`: los tres trabajos
terminaron SUCCESS. Se descargaron sus tres artefactos, verificando SHA-256
contra el digest publicado por GitHub, apertura del ZIP y contenido de los JUnit.

| Comprobación | Resultado comprobado |
|---|---|
| Formato y Ruff | Aprobados, incluidos src/tests/migrations/herramientas CI |
| mypy estricto | Aprobado, 72 módulos |
| Unitarias backend | 213, cero errores/fallos/omisiones; JUnit 4.644 s |
| Contratos de herramientas CI | 7 aprobados, comprobados también en el log del trabajo |
| Integración PostgreSQL/PostGIS y servicios reales | 119, cero errores/fallos/omisiones; JUnit 213.663 s |
| Frontend | 36, cero errores/fallos/omisiones |
| TypeScript y build | Aprobados; Vite conserva la advertencia conocida de tamaño |
| Compose, mapa y aplicación | Config válida, fuente exacta y car/MLD reales; API ready y cinco páginas HTTP 200 |
| Smoke original | Dos rutas, cuatro asignados, uno sin asignar; informe de la corrida sintética 8fb95ccd-24ab-48d4-9272-d0cb2c55499b |
| Retirada del stack GitHub | Nueve servicios, comprobadores de un solo uso, red y dos volúmenes exclusivos retirados |

Las 213 unitarias y 119 integraciones suman las 332 pruebas backend existentes,
sin duplicarlas dentro de esa ejecución; los siete contratos CI se cuentan aparte.
Los tiempos de JUnit describen esta muestra de pruebas, no rendimiento del solver
ni objetivos de capacidad. La ejecución conserva el smoke original y los casos
reales B2B/B2C; no se repitió una revisión visual ni las suites después de este
ajuste exclusivamente documental.

Copias locales recuperadas en `.ci-artifacts/run-37150811990`, fuera de los
candidatos; `.ci-artifacts/final-validation.json` resume SHA, trabajos, tiempos
e inventario de artefactos. La evidencia efectiva sigue siendo la ejecución de
GitHub y sus informes; los artefactos caducan a los 14 días.

### Limpieza local pendiente por revisión automática

La comprobación dirigida retiró correctamente `routeops-m41-frontend-host-check`.
La llamada posterior combinada para identificar y retirar el comprobador local,
su imagen y la copia restaurada de mapa fue rechazada antes de ejecutarse:
`blocked by policy`, sin razón más específica disponible. No se reintentó la
eliminación mediante otro mecanismo ni se cambiaron políticas.

La inspección de solo lectura confirmó que permanecen:

- Contenedor `routeops-m41-checks`, ID
  `097f06c5c403de7c8ce50f779616d393356f34584fc31c6e1cebdfce14c8c9f5`,
  comando de espera de comprobaciones, sin pertenecer al proyecto operacional.
- Imagen propia `routeops-m41-checks:review`.
- `.ci-work/osrm/santiago-demo.osm`, copia de 4 145 215 bytes restaurada y
  verificada; no es la fuente ni los índices operacionales de `data/osrm`.

La retirada de esos tres elementos sigue pendiente; no se declara completada.
Los informes se conservan y no hay configuración CI con contraseña pendiente
en `.ci-work`. Los nueve servicios locales, volúmenes, .env, originales privados,
fuente/índices operacionales y datos históricos se conservan.
El cierre agrega únicamente documentación tras el commit funcional comprobado.
No se repiten suites localmente por estos ajustes. El push genera una nueva
ejecución automática de CI, cuyo resultado debe comprobarse antes de integrar.

## Archivos de la entrega

El diff real contra main contiene 42 archivos: 25 Python existentes ajustados
solo por el formateador; workflow, imagen/contexto CI, overlay, fixture/lock de
mapa, seis herramientas Python/Bash, gitignore/gitattributes y documentación.
La lista exacta con sus hashes se conserva en `.ci-artifacts/candidate-files.json`.
No hay cambios frontend funcionales, dependencias nuevas ni migración nueva.
El cierre autoriza integrar en main; no retira ramas ni crea o mueve etiquetas.

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
El hostname adicional usa [la configuración oficial de Vite](https://vite.dev/config/server-options.html#server-allowedhosts).
Las actualizaciones futuras deben revisar release/notas/capacidades y sustituir
los SHA intencionalmente; no seguir una etiqueta móvil en el workflow.
