# Entrega 4.2 — rendimiento reproducible y seguridad básica

Candidata en `feat/m4-2-performance-security`, desde el cierre de 4.1
`3f4a7d6f24b7c174a76feadfff0fbf82db974958`. No integrada ni aceptada.
4.1 quedó publicada por fast-forward tras [CI aprobado](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37242798198).
Los cambios de cierre de 4.1 eran documentales: las suites solo se repitieron
automáticamente en GitHub. Main y la rama 4.1 coinciden; las cuatro etiquetas
previas no se movieron. Se conserva la rama 4.1 hasta cerrar el Hito 4.

## Alcance y fronteras

Uso local y de un usuario. No hay autenticación ni autorización: un programa
local puede utilizar la API sin Origin. Los puertos de Compose permanecen en
loopback. Un host remoto autorizado en configuración no convierte esto en
un despliegue público seguro. VROOM sigue exclusivamente en SolverGateway.
No se elevan límites, concurrencia de trabajadores, matrices ni timeouts.
No hay migración nueva, cambios de política ni modificación de datos históricos.

### Correcciones y comprobaciones de seguridad

| Frontera | Evidencia / cambio |
|---|---|
| Escritura desde navegador | Origin exacto permitido; se rechazan orígenes ajenos/null/duplicados y cross-site sin Origin antes de tocar datos. Los dos UI locales documentados en puerto 5173 se admiten siempre para el proxy de mismo origen, incluso con .env histórico que solo permita CORS localhost; no se amplían permisos de lectura CORS de ese .env. CLI sin Origin sigue funcionando. |
| DNS rebinding | Host explícito localhost/127.0.0.1/IPv6 loopback/backend. testserver se añade solo en entorno test. Sin wildcard ni confianza en X-Forwarded-Host. |
| Cuerpo no multipart | Límite fijo de 1 MiB, Content-Length validado y conteo real sin confiar en él, antes de parsear JSON/escribir. La entrada geográfica conserva su límite de 10 000 vértices; no se altera el contrato de escenarios/revisiones. |
| Multipart | Aplicación limita a 2 048 bytes de nombres/valores y ocho encabezados por parte; comprueba longitud declarada antes de recibir. La versión fijada python-multipart ya tenía sus propios límites de 4 224 bytes y ocho encabezados: no se presenta el estado anterior como ilimitado. Se conservan límites por archivo/paquete, streaming y limpieza tras interrupción. |
| XML en XLSX | El filtro DTD/ENTITY anterior podía esquivarse con UTF-16/32. Se revisan también los bytes sin separadores NUL, incluida lectura incremental con tokens partidos. No se evalúan fórmulas/enlaces ni se extraen rutas del ZIP. |
| Almacenamiento | Se conservan claves UUID opacas, permisos 0700/0600, O_NOFOLLOW/O_EXCL, hashes, mantenimiento/concesiones y ausencia de descarga de originales. Pruebas existentes de rutas, enlaces, expansión, fórmulas y huérfanos siguen en CI. |
| Exportación | Se conservan XLSX de texto inerte, neutralización CSV, nombre generado a partir de UUID, no-store/nosniff y límite de celdas. Se verifica ZIP/CRC real en cada muestra de exportación. |
| Errores/logs | API y los cuatro trabajadores comparten el formateador; los handlers de error de Uvicorn lo reutilizan y su access log crudo se sustituye por el evento HTTP estructurado. httpx/httpcore no registran URLs en INFO. Logs guardan tipo de excepción y marcos archivo base/función/línea; no mensaje de excepción, parámetros SQL, código fuente ni rutas locales. SQLAlchemy usa hide_parameters. X-Request-ID se valida y los logs HTTP usan la plantilla de ruta, sin query/identificadores dinámicos. Los errores 422 incluyen campo/tipo sin input/contexto/mensajes derivados de valores privados. |
| Contenedores | Backend/trabajadores ya no eran root. Frontend pasa a node; esos seis servicios pierden todas las capabilities y usan no-new-privileges. Se mantienen imágenes del solver y base de datos; no se fuerza un cambio de usuario que rompa su inicialización. |

Los rechazos tienen códigos estables ORIGIN_NOT_ALLOWED, REQUEST_BODY_LIMIT,
REQUEST_LENGTH_INVALID y MULTIPART_HEADER_LIMIT; errores del parser multipart
conservan MULTIPART_INVALID. Middleware de Host devuelve 400. La protección no
autoriza datos ni protege contra malware/programas del propio usuario. OSRM/VROOM
expuestos en loopback siguen sin autenticación. El dev server Vite no es un
servidor productivo; no se habilitan hosts arbitrarios.

## Reproducción de rendimiento

`backend/scripts/benchmark_acceptance.py` reutiliza `_planning_files` del benchmark
HTTP de 2.6 y generadores CSV/XLSX de `benchmark_import_flow.py`. Semilla 41042,
fecha/horizonte/moneda/zona explícitos; hash de cada archivo y filas por dataset.
Perfiles completos: 2/10/20 pedidos, tres líneas por pedido, 1/2/4 CD y
1/3/6 vehículos. B2B/B2C declaran vehículo y servicio, sin restricciones ocultas.
Inventario adicional: 1 000 y 10 000 posiciones, CSV y XLSX; esos grupos miden
importación/publicación, no llaman al solver ni simulan capacidad máxima. Usan
la misma fixture antigua de inventario (un pedido/vehículo, servicio 10 min);
las etiquetas B2B/B2C de esos cuatro grupos no representan diferencias de modelo.
La diferenciación truck/van y 5/1 min corresponde a los seis flujos completos.

Cada grupo registra preparación, un calentamiento excluido y tres repeticiones.
Concurrencia 1 en muestras pequeñas/B2B mediana, 2 en B2C mediana y ambos casos
de 20 pedidos. Cada cliente tiene escenario/stock propio: compite por API,
trabajadores PostgreSQL, solver y recursos, no comparte artificialmente stock.
La competencia por las mismas posiciones se verifica separadamente en CI.
Los triggers permanecen activos en la publicación.

Comandos sobre un proyecto exclusivo `routeops-ci-*` con puerto efímero:

```bash
PYTHONPATH=backend/src python backend/scripts/benchmark_acceptance.py \
  --base-url http://127.0.0.1:PUERTO --project routeops-ci-ID \
  --output .ci-artifacts/benchmark-http.json --repetitions 3 --seed 41042
PYTHONPATH=backend/src python backend/scripts/benchmark_acceptance.py \
  --base-url http://127.0.0.1:PUERTO --project routeops-ci-ID \
  --output .ci-artifacts/benchmark-import.json --repetitions 3 --seed 41042 --imports-only
```

El programa contrasta el puerto Docker real y rechaza apuntar a RouteOps
operacional. No sobrescribe informes. Conserva muestras originales, errores,
identificadores, solicitudes/resultados, ambiente, imágenes y variabilidad
min/mediana/max/desviación estándar. Un error o memoria no observable falla el
benchmark: no se borra una muestra para mejorar el resultado.

GitHub Actions permite `workflow_dispatch` con `benchmarks=true`, después de
las integraciones y smoke, dentro del mismo stack descartable. El trap sanea
XML/TXT y JSON de benchmark, retira solo el proyecto y conserva los informes
14 días. Push/PR ordinarios no repiten benchmarks; un commit que solicite
explícitamente `[benchmarks]` activa la medición en su ejecución automática,
evitando otra suite integral solo para disparar la medición.

### Qué se mide y qué permanece desconocido

- Latencia HTTP medida con perf_counter incluye respuesta completa y su commit
  confirmado cuando corresponde. Validación/planificación/comparación observadas
  por polling incluyen espera de cola y hasta 200 ms de retraso de observación.
- Planificación conserva métricas persistidas: cola inicial, intentos/fases,
  recuperación y total hasta transición anterior al COMMIT final. El ACK de ese
  commit sigue desconocido: no se añade al total ni se confunde con latencia HTTP.
- Validación no expone timestamps de claim/fases; comparación expone historia,
  pero no duración activa monotónica. No se calcula procesamiento restando cola
  de observaciones de polling ni se inventan intervalos después de interrupciones.
- Publicación HTTP incluye reproducción e inserción/commit. El benchmark directo
  anterior sigue disponible para medir reproducción por separado en una base
  descartable; el endpoint no expone esa fase, que se declara desconocida aquí.
- Tiempos operativos de ruta pertenecen al plan; no son tiempo de cálculo.
- RSS/working set del cliente cada 50 ms; docker stats (working set, usualmente
  descontando caché) y /proc de procesos Python de los cuatro servicios con Python
  cada varios segundos. Son máximos muestreados, no picos exactos ni suma de RSS
  entre procesos con memoria compartida. PostgreSQL/OSRM/VROOM tienen métricas
  del contenedor; no se presentan como RSS individual. Se registran timestamps
  reales y fallos de muestreo. tracemalloc es solo asignación Python del cliente,
  sin librerías nativas ni servicios, y se registra por separado.

Las 80 celdas CD–pedido de la muestra 20×4 son de asignación. Para rutear, el
límite conservador usa (pedidos + dos extremos por vehículo)²: (20+12)²=1 024.
VROOM puede deduplicar ubicaciones/particionar por CD; esto no transforma la
matriz rectangular de asignación en la matriz de rutas. Límites existentes se
comprueban antes de crear corridas/reservas; no se equiparan a límites de importación.

## Dependencias e imágenes

Consultar avisos sin modificar locks:

```bash
python scripts/security/audit_dependencies.py --output .ci-artifacts/dependency-python.json
cd frontend
npm audit --json --package-lock-only --ignore-scripts > ../.ci-artifacts/dependency-frontend.json
```

OSV contra 46 versiones de ambos locks y npm audit no devolvieron avisos en la
consulta inicial 2026-10-04. Esto no equivale a imagen libre de vulnerabilidades.
CI captura nuevamente ambos informes, falla ante avisos o consulta incompleta
y no ejecuta audit fix ni oculta alertas. Las bases de advisories cambian: no se
afirma un resultado perpetuo ni auditoría exhaustiva.

Docker Scout 1.24.0 analizó las cinco referencias locales iniciales mediante
`docker scout cves --format sarif --output INFORME image://IMAGEN`.
Sus SARIF completos se conservan fuera del commit en `.ci-artifacts`; el
inventario [versionado](research/m42-security-inventory.json) separa
imagen/paquete/aviso, versión corregida y revisión
de aplicabilidad. Incluye herramientas vendorizadas de pip/npm no incluidas en
los locks. No se filtran avisos de severidad baja/indefinida.

La consulta primaria de [OpenSSL CVE-2026-75803](https://security-tracker.debian.org/tracker/CVE-2026-75803)
confirma fix bookworm 3.0.22-1~deb12u1 y describe EVP_Cipher con AEAD vacío.
RouteOps no invoca ese API ni emplea AEAD propio; esto limita la vía directa,
pero no acredita toda la imagen como segura. Se mantiene el aviso/fix pendiente,
sin sustituir masivamente Python, Debian ni imágenes OSRM/VROOM aprobadas.
El aviso [zlib CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091)
se refiere a escritura gz no bloqueante; RouteOps importa ZIP por lectura y
exporta a buffers, sin gzprintf. Debian lo mantiene abierto: no se marca fixed
por una inferencia de alcance. Setuptools PackageIndex.download no forma parte
del runtime de RouteOps: no se aceptan sdist ni URL de paquetes desde la API.
Perl/util-linux/git/apt y herramientas de imágenes upstream requieren revisión
del productor; no se ejecutan contenidos importados ni comandos shell de usuario.
La inspección del backend aislado confirmó que urllib3 2.7.0 está vendorizado
por pip y no está importable como paquete de aplicación. RouteOps usa httpx;
esto diferencia los avisos de herramientas de instalación del runtime, sin
marcarlos corregidos ni acreditar una descarga de paquetes segura.
Otros avisos siguen pendientes de análisis específico; no se declara ausencia
de explotación solo por la publicación local. El inventario conserva esa incertidumbre.

## Resultados medidos y evidencia

Commit funcional comprobado: `88f5398517a5a274ee02a8c986720d01b80833f3`.
[CI 37245515724](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37245515724) terminó **success**: 230 unitarias + 120 integraciones backend (350), 36 frontend y ocho contratos de herramientas CI. JUnit recuperados: cero fallos, errores y omisiones. Ruff formato/lint, mypy estricto (74 módulos), TypeScript, Vite, Compose, migraciones ascendente/descendente y smoke real aprobados.
El smoke original devolvió dos rutas, cuatro entregas asignadas y una excepción. Las integraciones conservan B2B/B2C, matrices/límites, compatibilidad histórica, diagnósticos, exportaciones, recuperación/propietario tardío, rollback, reservas externas y carreras de stock/aceptación/cancelación. No se repitieron suites locales completas: 63 pruebas dirigidas más la integración HTTP durante los ajustes.

Entorno CI: Ubuntu 24.04.5, kernel 6.17.0-1022-azure, cuatro CPU, Docker 28.0.4 y 15,6 GiB de memoria del daemon. Python 3.14.7; httpx 0.28.1, FastAPI 0.141.1, SQLAlchemy 2.0.54, psycopg 3.3.6, Starlette 1.7.0, python-multipart 0.0.32; Node 24.21.0. Imágenes/perfil/extracto de 4.1 sin sustitución, checksum OSM aprobado.
Los logs de build recuperados identifican también los digests efectivos de Python
y Node, incluidos en el resumen; los tags de esas bases pueden reconstruirse,
por lo que la versión nominal por sí sola no garantiza el mismo sistema base. Preparación de imágenes, migraciones y mapa fuera del benchmark; cada grupo conserva su preparación y calentamiento. Muestras HTTP: 2026-10-05 00:01:27–00:05:52 UTC; inventario: 00:05:53–00:07:17 UTC.

[Resumen verificable](research/m42-performance-results.json): filas, tamaños, hashes de entradas e informes, semilla, n, mínimos, medianas, máximos y desviación estándar. El hash del harness recuperado coincide con el blob del commit probado. Los originales completos se conservan en los artefactos CI (14 días) y en la carpeta externa `C:\Users\erosi\Desktop\routeops-m4-2-evidence`, con manifest/SHA-256. No hay capturas nuevas: esta entrega aporta pruebas API/automatizadas y mediciones directas; conserva la evidencia visual aceptada.

### HTTP: mediana y rango observado, segundos

Las seis primeras filas incluyen planificación/comparación y cuatro exportaciones por muestra. Tres repeticiones por grupo; n=6 con dos clientes. El inventario adicional tiene un pedido/una línea/un CD/un vehículo y n=3. Total: **39 muestras medidas y diez calentamientos, cero errores**.

| Caso / formato | Pedidos / vehículos / inventario | Clientes / n | Bytes | Carga HTTP | Publicación HTTP con reproducción | Exportación comparación XLSX |
|---|---:|---:|---:|---:|---:|---:|
| B2B small CSV | 2 / 1 / 3 | 1 / 3 | 1,238 | 0.014 (0.014–0.121) | 0.023 (0.021–0.026) | 0.122 (0.116–0.200) |
| B2C small XLSX | 2 / 1 / 3 | 1 / 3 | 3,957 | 0.010 (0.010–0.011) | 0.025 (0.022–0.027) | 0.115 (0.113–0.115) |
| B2B medium XLSX | 10 / 3 / 6 | 1 / 3 | 5,101 | 0.010 (0.010–0.011) | 0.029 (0.028–0.038) | 0.464 (0.461–0.467) |
| B2C medium CSV | 10 / 3 / 6 | 2 / 6 | 3,132 | 0.023 (0.017–0.024) | 0.047 (0.042–0.066) | 0.514 (0.455–0.595) |
| B2B bounded CSV | 20 / 6 / 12 | 2 / 6 | 5,682 | 0.025 (0.018–0.055) | 0.051 (0.036–0.055) | 1.343 (1.044–1.669) |
| B2C bounded XLSX | 20 / 6 / 12 | 2 / 6 | 6,548 | 0.018 (0.012–0.018) | 0.068 (0.065–0.088) | 1.293 (0.954–1.831) |
| B2B inventory-1000 CSV | 1 / 1 / 1000 | 1 / 3 | 50,807 | 0.015 (0.014–0.015) | 0.140 (0.130–0.151) | No aplica |
| B2C inventory-1000 XLSX | 1 / 1 / 1000 | 1 / 3 | 26,607 | 0.010 (0.010–0.011) | 0.184 (0.169–0.211) | No aplica |
| B2B inventory-10000 CSV | 1 / 1 / 10000 | 1 / 3 | 500,807 | 0.015 (0.015–0.016) | 1.254 (1.219–1.275) | No aplica |
| B2C inventory-10000 XLSX | 1 / 1 / 10000 | 1 / 3 | 230,229 | 0.012 (0.012–0.012) | 1.588 (1.576–1.734) | No aplica |

CSV de corridas: 0,018–0,562 s; XLSX de corridas: 0,045–0,562 s; CSV de comparaciones: 0,027–0,687 s. Sus estadísticas por grupo y originales están en el resumen/informes; no se omiten los máximos de clientes concurrentes.

### Cola, procesamiento y operación, separados

Columnas: mediana (mínimo–máximo), segundos. La observación incluye polling; activo es medición monotónica persistida. El total persistido se corta **antes del COMMIT final** y su ACK durable sigue null/UNKNOWN. No se suman fases para fabricar un total.

| Caso | Validación observada | Planificación observada | Cola inicial | Activo de planificación | Total anterior a COMMIT | Comparación observada | Duración operativa agregada de rutas |
|---|---:|---:|---:|---:|---:|---:|---:|
| B2B small | 4.164 (3.755–4.366) | 2.092 (1.880–2.303) | 1.884 (1.686–2.055) | 0.125 (0.124–0.136) | 2.012 (1.815–2.195) | 3.567 (3.562–3.577) | 817.000 (817.000–817.000) |
| B2C small | 3.737 (3.544–3.746) | 2.500 (2.493–2.701) | 2.353 (2.303–2.501) | 0.128 (0.125–0.150) | 2.483 (2.459–2.633) | 3.565 (3.563–3.569) | 337.000 (337.000–337.000) |
| B2B medium | 2.495 (2.296–2.712) | 3.141 (3.121–3.331) | 2.910 (2.776–3.069) | 0.168 (0.157–0.175) | 3.082 (2.937–3.247) | 3.595 (3.585–3.791) | 4366.000 (4366.000–4366.000) |
| B2C medium | 1.058 (0.659–1.923) | 4.121 (3.817–4.661) | 3.796 (3.475–4.286) | 0.170 (0.165–0.178) | 3.974 (3.645–4.467) | 3.682 (3.629–3.731) | 1966.000 (1966.000–1966.000) |
| B2B bounded | 3.185 (2.552–5.088) | 0.996 (0.481–5.062) | 0.631 (0.058–4.773) | 0.225 (0.208–0.243) | 0.871 (0.281–4.995) | 4.079 (3.902–4.182) | 8237.000 (8237.000–8237.000) |
| B2C bounded | 1.288 (0.866–2.969) | 2.053 (1.512–2.578) | 1.658 (1.217–2.167) | 0.218 (0.194–0.252) | 1.912 (1.416–2.381) | 4.458 (4.282–4.594) | 3437.000 (3437.000–3437.000) |

Se observan colas de hasta 4,773 s frente a 0,124–0,252 s activos de planificación. Parte de la variación corresponde al polling actual de trabajadores y al solapamiento de clientes. Los perfiles B2B/B2C se ejecutaron en secuencia con distinto servicio/tipo de vehículo: no permiten atribuir causalmente diferencias de latencia a un modelo. No se elevan trabajadores, límites ni timeouts a partir de estas muestras.

La reproducción se midió además con el benchmark directo existente, en ocho procesos/bases descartables locales: calentamiento + tres repeticiones por CSV/XLSX, 10 000 posiciones, triggers activos. CSV: reproducción 0,474–0,497 s (mediana 0,487); inserción/commit restante 1,851–1,962 s; publicación total 2,326–2,449 s. XLSX: reproducción 1,496–1,545 s (mediana 1,508); restante 1,854–1,924 s; total 3,398–3,432 s. El restante es una resta de duración total y wrapper de reproducción, incluye más trabajo que INSERT y no se presenta como timer puro de SQL. Se conservaron stdout/stderr/códigos de salida y el hash del harness. Sin muestreo del PostgreSQL operacional ni llamada al solver en este ensayo.

### Memoria real: máximos muestreados, MiB

No son picos exactos. La primera columna es working set de contenedor (Docker descuenta caché); la segunda es el máximo RSS de un proceso observado por /proc, sin sumar páginas compartidas ni incluir la sonda. Diferencias entre ambas medidas son esperables; no se equiparan.

| Servicio | Contenedor, máximo entre grupos CI | Proceso RSS, máximo entre grupos CI |
|---|---:|---:|
| backend | 215.6 | 242.2 |
| import-validation-worker | 77.4 | 91.9 |
| planning-worker | 75.6 | 94.4 |
| comparison-worker | 78.6 | 97.3 |
| database | 153.1 | No observado individualmente |
| osrm | 10.4 | No observado individualmente |
| vroom | 53.4 | No observado individualmente |

Cliente RSS: 81,6–112,6 MiB entre grupos; tracemalloc separado: 0,8–11,9 MiB. RSS incluye entradas preparadas y resultados acumulados; tracemalloc excluye preparación/calentamiento y no mide memoria nativa ni servicios. Hubo 4–10 muestras de servicios por grupo, con timestamps originales; ningún error de muestreo. API creció de 106,9 a 215,6 MiB de working set entre perfiles completos y volvió a ~189–191 MiB en los grupos de inventario: se registra, sin concluir ausencia/presencia de fuga a partir de una sesión corta.

Ensayo exploratorio Windows: Python 3.14.7, Docker 29.8.1, 16 CPU y 15,4 GiB, con servicios operacionales disponibles y Scout solapado al inicio. También 49 flujos sin errores; publicación de 10 000 posiciones CSV 2,686–2,820 s/XLSX 3,920–4,012 s; API hasta 285,9 MiB de contenedor. Ese ensayo precedió el ajuste de metadata XLSX y exclusión de la sonda RSS: conserva los hashes/resultados originales y no se atribuye al commit final. No se combinan ambos ambientes como una distribución homogénea ni se promete identidad binaria de ZIP entre plataformas.

### Hallazgos y límites de la evaluación

- Locks: OSV (46 versiones) y npm audit completos en CI, sin advisories en ese instante; las herramientas consultan nuevamente en cada CI y fallan ante avisos/incompletitud. No hay audit fix ni supresión.
- Scout inicial se conserva como registro histórico. La [evaluación final de imágenes](milestone-4-2-image-security-review.md) lo amplía con las cinco imágenes candidatas y checks, IDs/digests exactos, 47 familias prioritarias, backports Debian y vendors. Se mitigaron Unicode XLSX y la frontera HTTP de VROOM; su Node 20 se sustituyó selectivamente por Node 24 fijado, sin cambiar solver/perfil/dataset. Quedan CVEs sin parche y condiciones sin vía observada: no se presentan como corregidas ni se suprimen. El inventario inicial remite al inventario final y no constituye por sí solo la conclusión actual.
- Se verificaron flujos representativos, no todas las combinaciones ni máximos de importación/exportación. No hay garantía de capacidad, percentil contractual, prueba de estrés prolongada ni comparación estadística entre modelos. Tres repeticiones permiten ver variación, no dimensionar producción.
- No se agregan objetivos numéricos arbitrarios ni cuotas de CPU/memoria sin evidencia. Se conservan límites actuales y las mediciones como baseline reproducible para cambios selectivos futuros.
- No hay revisión visual nueva ni autenticación: alcance exclusivamente local/un usuario. Las protecciones Origin/Host no sustituyen autorización.
- Starlette/httpx TestClient mantiene advertencia de deprecación y Vite mantiene la advertencia conocida del tamaño del bundle; pruebas sin omisiones. Una invocación inicial de los contratos CI desde Windows falló al reemplazar un informe de solo lectura (WinError 5); el contrato Linux requerido pasó, ocho pruebas, localmente en contenedor y en GitHub, sin cambiar permisos ni su expectativa.

Las mediciones no revelaron un fallo que requiera aumentar límites o modificar políticas/reservas. Las correcciones se concentran en fronteras locales de entrada/logs/contenedores. 4.2 queda candidata a revisión, no integrada ni aceptada; 4.3 no comenzó.

## Recursos e incertidumbres conservadas

No se reintenta la eliminación rechazada de 4.1: routeops-m41-checks,
routeops-m41-checks:review y .ci-work/osrm/santiago-demo.osm permanecen registrados.
No condicionan el cierre técnico. Los servicios/volúmenes originales y evidencia
de hitos anteriores permanecen. El proyecto propio routeops-ci-m42-f201ef51
se retiró tras recuperar la evidencia: nueve contenedores, dos volúmenes
sintéticos y una red; se eliminó su env efímero. Se conservan las cachés de
build, copia de mapa de ese proyecto y entorno Python ignorado para reproducir.
No hubo limpieza global ni retirada de los recursos bloqueados de 4.1. Scout informó dificultades para retirar algunos
archivos de su caché Windows por archivo en uso; se documentan sin limpieza general.
No hay autenticación, multiusuario, despliegue público, revisión visual nueva,
certificación de seguridad, capacidad máxima garantizada ni inicio de 4.3.

## Archivos de la entrega

33 archivos respecto de main, incluidos los nuevos. Fuentes, pruebas, configuración
y resumen documental; no se versionan originales privados, logs, venv ni
informes completos de verificación.

La continuación de imágenes añade seis archivos a aquella lista: las cuatro
fuentes/contratos del wrapper (`infrastructure/vroom/.dockerignore`, `Dockerfile`,
`request-guard.cjs`, `request-guard.test.cjs`), el informe de imágenes y
`docs/research/m42-image-security-assessment.json`. También actualiza seguridad
XML, sus pruebas, la integración HTTP, Compose, CI y documentación ya enumerados. Son 39 archivos
en la entrega acumulada respecto de main. Los benchmarks y sus resultados no
se modificaron ni repitieron; corresponden al baseline previo a Node/guards.

- `.env.example`
- `.github/workflows/quality.yml`
- `README.md`
- `backend/Dockerfile.ci`
- `backend/Dockerfile.ci.dockerignore`
- `backend/scripts/benchmark_acceptance.py`
- `backend/scripts/benchmark_http_flow.py`
- `backend/src/routeops/api/logging.py`
- `backend/src/routeops/api/main.py`
- `backend/src/routeops/api/security.py`
- `backend/src/routeops/application/import_upload.py`
- `backend/src/routeops/application/import_validation.py`
- `backend/src/routeops/infrastructure/config.py`
- `backend/src/routeops/infrastructure/logging.py`
- `backend/src/routeops/infrastructure/persistence/comparison_worker.py`
- `backend/src/routeops/infrastructure/persistence/planning_worker.py`
- `backend/src/routeops/infrastructure/persistence/session.py`
- `backend/src/routeops/infrastructure/persistence/validation_worker.py`
- `backend/src/routeops/infrastructure/storage/maintenance.py`
- `backend/tests/conftest.py`
- `backend/tests/integration/test_local_security_http.py`
- `backend/tests/unit/test_benchmark_acceptance.py`
- `backend/tests/unit/test_security_boundaries.py`
- `docker-compose.yml`
- `docs/milestone-4-2-performance-security.md`
- `docs/research/m42-performance-results.json`
- `docs/research/m42-security-inventory.json`
- `docs/roadmap.md`
- `frontend/Dockerfile`
- `scripts/ci/integration.sh`
- `scripts/ci/sanitize_reports.py`
- `scripts/ci/test_tools.py`
- `scripts/security/audit_dependencies.py`
