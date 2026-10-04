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
importación/publicación, no llaman al solver ni simulan capacidad máxima.

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
Otros avisos siguen pendientes de análisis específico; no se declara ausencia
de explotación solo por la publicación local. El inventario conserva esa incertidumbre.

## Validación y estado de resultados

En preparación: CI del commit de entrega y mediciones repetidas. Evidencia local
dirigida: 63 pruebas de contratos/importación/seguridad/fixtures, Ruff formato/lint, mypy
74 módulos; comprobación HTTP real de rechazo antes de efectos SQL aprobada.
Los resultados finales se completan tras la ejecución, no se presuponen.

## Recursos e incertidumbres conservadas

No se reintenta la eliminación rechazada de 4.1: routeops-m41-checks,
routeops-m41-checks:review y .ci-work/osrm/santiago-demo.osm permanecen registrados.
No condicionan el cierre técnico. Los servicios/volúmenes originales y evidencia
de hitos anteriores permanecen. Scout informó dificultades para retirar algunos
archivos de su caché Windows por archivo en uso; se documentan sin limpieza general.
No hay autenticación, multiusuario, despliegue público, revisión visual nueva,
certificación de seguridad, capacidad máxima garantizada ni inicio de 4.3.
