# Entrega 4.2 — evaluación de imágenes y componentes

Revisión del 4 de octubre de 2026, con registros UTC del 5 de octubre.
Entrega técnicamente aceptada para uso local de un usuario en
`feat/m4-2-performance-security`, commit revisado
`fa02d7133bc2bb2f15027960f854eb16e27bcd37`.

## Conclusión de cierre

El usuario aceptó técnicamente 4.2 para el alcance **local, un usuario y datos
sintéticos/confiables de preparación**. CI del último ajuste funcional pasó.
No se encontró una vía aplicable conocida que quede sin mitigación y deba
impedir ese cierre. Esto no equivale a certificar las imágenes libres de CVEs:
quedan paquetes sin parche, incertidumbre de alcance transitivo y seguimiento
de dependencias de instalación. No es una autorización para despliegue público.

Tres hallazgos aplicables recibieron corrección selectiva:

1. **XML XLSX y Expat vendorizado:** Python 3.14.7 contiene Expat 2.8.2. El
   [aviso oficial](https://github.com/libexpat/libexpat/pull/1282) explica la
   aceptación de pares UTF-16 mal formados. Se comprueba Unicode estrictamente
   antes del parser, tanto en metadata como en hojas incrementales, con/sin BOM
   y declaración, endian LE/BE, pares partidos y EOF. Se conserva el error
   controlado `XLSX_INVALID`. El binario Expat **no se actualizó**: es una
   mitigación del camino de importación; upstream 2.8.5 corrige la biblioteca.
2. **Frontera HTTP de VROOM:** su wrapper permitía CORS `*` y formularios
   `urlencoded`, accesibles desde navegador contra el puerto loopback. El nuevo
   preload Node rechaza Host ajeno/rebinding, Origin ajeno o `null`, cross-site,
   formularios, query y métodos no admitidos antes de Express/solver. Mantiene
   JSON, llamadas internas sin Origin y `/health`; conserva el límite 1 MiB.
   Host/Origin no sustituyen autenticación ni impiden un proceso local malicioso.
3. **Runtime Node de VROOM:** Node 20.20.1 está
   [fuera de soporte](https://nodejs.org/en/about/previous-releases). La imagen
   derivada copia **sólo** el ejecutable de Node 24.21.0, de la misma imagen
   oficial fijada por digest que utiliza el frontend. No actualiza npm, Express
   ni el conjunto de dependencias para hacer desaparecer avisos.

El SHA-256 de `/usr/local/bin/vroom` es idéntico antes/después:
`929f1dd11c077a48fe73971538ef26f8fc4dc09ae532a4e7399efda616ba086f`.
VROOM 1.15.0 sigue exclusivamente tras `SolverGateway`. OSRM 26.9.0, MLD,
`car.lua`, configuración, dataset y sus checksums no cambiaron; no se requiere
regeneración por sustituir el runtime HTTP Node. CI reproduce su preparación
con recursos aislados y comprueba routing real.

## Qué se escaneó

El [inventario final](research/m42-image-security-assessment.json) contiene los
IDs/digests completos, revisión fuente, rol, comandos, usuario, SHA-256 SARIF,
comparaciones Debian y fichas de 47 familias prioritarias. Se reconstruyeron las
imágenes de aplicación desde el commit funcional final con etiquetas propias;
no se sustituyeron ni recrearon los contenedores operacionales históricos.

| Imagen | Rol | Avisos únicos de Scout por imagen |
|---|---|---:|
| backend | aplicación, usuario routeops | 82 |
| frontend | aplicación, usuario node | 88 |
| VROOM derivada | runtime HTTP protegido, solver fijado | 357 |
| OSRM | runtime upstream fijado por digest | 80 |
| PostgreSQL/PostGIS | runtime upstream fijado por digest | 281 |
| checks | ejecución de pruebas; no servicio operacional | 82 |

Los números no suman vulnerabilidades distintas: hay duplicados entre imágenes,
paquetes y aliases CVE/GHSA. Se agrupan por ecosistema y componente fuente;
los aliases y cada copia instalada permanecen trazables. Scout produjo 370
resultados en VROOM y 282 en DB porque algunos avisos tienen varias copias.
No hay supresiones. Se revisaron todos los avisos con CVSS >=7, incluidos los
`UNSPECIFIED` de Debian; no se interpretó esa etiqueta como ausencia de riesgo.

Scanner: Docker Scout 1.24.0, SARIF y SBOM completos. El primer intento paralelo
del frontend falló por lock de caché (`cache may be in use ... timeout`); se
conservó ese log y el escaneo secuencial terminó con código 0. Fue un fallo de
herramienta, sin aprobación ficticia ni cambio de políticas. Los IDs locales
reconstruidos no se presentan como los IDs efímeros de los builds anteriores de
GitHub Actions. Los contenedores históricos y la imagen checks de 4.1 se
distinguen explícitamente en el inventario; no pertenecen a esta reconstrucción.

Reproducción, desde el checkout correspondiente, usando etiquetas nuevas:

```sh
docker build -t routeops-review-backend backend
docker build -t routeops-review-frontend frontend
docker build -t routeops-review-vroom infrastructure/vroom
docker build -f backend/Dockerfile.ci -t routeops-review-checks backend
docker scout cves --format sarif --output backend.sarif image://routeops-review-backend
docker scout sbom --format json --output backend-sbom.json image://routeops-review-backend
# Repetir cves/sbom para frontend, VROOM, checks y las referencias upstream
# exactas de OSRM y PostgreSQL/PostGIS del inventario, sin montar volúmenes.
```

## Evidencia de distribución y vendors

Se obtuvieron `/etc/os-release`, `dpkg-query` (paquete binario, versión, source y
source-version), versiones de intérpretes/vendors, `ldd`, árbol npm sin dev,
source del wrapper y probes de fuentes oficiales. Se compararon 171 instancias
Debian con `dpkg --compare-versions`, conservando epochs, backports, `~` y
`really...`: 24 con fix instalado, 89 con fix disponible no instalado y 58 sin
fix confirmado. Esos contadores incluyen instancias repetidas; son estado del
paquete, no medida de explotabilidad. La excepción MiniZip se registra aparte.

- **VROOM es mixto:** os-release trixie, pero OpenSSL 3.0.18, curl
  7.88.1-10+deb12u14 y varias bibliotecas provienen de bookworm. glibc
  2.41-12+deb13u2 y gcc14 tienen otra procedencia. No se aplicaron sin más los
  rangos trixie del purl del scanner a esas copias.
- **curl:** el [aviso upstream](https://curl.se/docs/CVE-2023-38545.html) requiere
  SOCKS5 con resolución remota y hostname largo. Debian
  [incorporó el fix en deb12u4](https://security-tracker.debian.org/tracker/CVE-2023-38545);
  la copia u14 está corregida para ese CVE. Otros avisos recientes siguen
  pendientes; no se marcó toda la familia como resuelta.
- **MiniZip:** Debian señala que
  [contrib/minizip no se compila en esos binarios bookworm](https://security-tracker.debian.org/tracker/CVE-2023-45853).
  No es correcto atribuir ese CVE al zlib1g instalado sólo por source/version.
  La copia minizip trixie de DB se compara separadamente con el fix.
- **OpenSSL:** Python usa 3.0.20 de distro; el solver nativo enlaza 3.0.18;
  PostgreSQL tiene 3.5.6. **psycopg_binary usa 3.5.8 vendorizado**, comprobado
  mediante su libcrypto, y Node final también incorpora 3.5.8. No son la misma
  copia. [Los avisos upstream](https://openssl-library.org/news/vulnerabilities/)
  exigen distinguir DTLS, CMS/CMP/PKCS12, funciones AEAD específicas y TLS.
  RouteOps usa HTTP interno/TCP; `SHOW ssl` y `SHOW log_hostname` devolvieron
  `off` en PostgreSQL existente, mediante consulta de sólo lectura. Quedan fixes
  de distro pendientes; si se habilita TLS hay que revisar nuevamente los
  paths de certificados/negociación, no reutilizar esta ausencia de vía.
- **PCRE2:** los [avisos JIT](https://security-tracker.debian.org/tracker/CVE-2026-103111)
  requieren regex controlada y uso específico del motor; Python/Node usan
  otros motores y las APIs no reciben regex de usuario. OSRM/VROOM no enlazan
  PCRE2 dinámicamente. Ello no parchea los paquetes presentes.
- **Go:** se identificaron `tsc` de TypeScript 7.0.2 (Go 1.26.4) y `gosu` 1.19
  (Go 1.24.6). El primero compila fuentes confiables; el segundo cambia usuario
  antes de PostgreSQL. No son servidores Go de TLS/HTTP que procesen originales.
  Se conservaron los avisos y sus funciones/condiciones oficiales; no se afirma
  que todas ellas estén eliminadas del binario. Las condiciones Windows no
  se cumplen en los binarios linux/amd64 evaluados.
- **Tooling:** npm tar/pacote/sigstore, pip setuptools/urllib3/msgpack y dev
  dependencies de VROOM están instaladas y continúan con avisos. `npm start`
  ejecuta un script fijo, no instala paquetes desde requests. No se ejecutan
  PackageIndex, git-js ni extracción TAR con archivos del usuario. pip msgpack
  usa fallback Python, no el Unpacker nativo del aviso. Las copias npm/Node y
  pip/vendor se separan: un lock de aplicación sin advisories no acredita
  todas las dependencias incluidas en las imágenes.

Los advisories se contrastaron con el JSON oficial Debian y registros OSV con
referencias de mantenedores. Para ocho CVEs cuyo lookup OSV no estaba disponible
se recuperó el registro CNA del repositorio oficial CVEProject/cvelistV5.
GMS-2020-2 (execa) conserva incertidumbre de fuente: no se recuperó un registro
oficial equivalente. No se suprimió ni se llamó corregido; su condición descrita
por Scout se separa de la evidencia de no uso en el server. Las fuentes y sus
hashes están en el paquete externo.

## Validación y límites

- 86 pruebas locales dirigidas de importación, contexto y seguridad: aprobadas;
  incluyen 30 casos nuevos de Unicode. Ruff/formato y mypy estricto (74 fuentes)
  aprobados. Sin migraciones, cambios de contratos de solver o reservas.
- Tres contratos Node del guard aprobados tanto en Node original como en Node
  24. Una integración HTTP adicional pasó contra el wrapper real aislado:
  Host/Origin/form/query rechazados y health conservado. Su contenedor
  `routeops-m42-vroom-guard-review`, sin volúmenes de datos, se detuvo y retiró
  mediante su `--rm`. No tocó bases, reservas ni originales operacionales.
- CI de los dos avances iniciales pasó: [guard/Unicode](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37248648397)
  y [runtime Node](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37249001857).
  [CI final funcional 37249531924](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37249531924)
  pasó en `cecc32980ed515f234dc861ab8f6ed343ddc90ff`: 260 unitarias backend +
  121 integraciones = **381 backend**, 36 frontend, ocho contratos de herramientas
  CI y tres contratos Node. JUnit recuperados y hashes de artefactos verificados,
  sin fallos ni omisiones. Ruff/formato, mypy, TypeScript, build y Compose pasaron.
  [CI del commit documental aprobado `fa02d71`](https://github.com/ErosIgnacio/routeops-platform/actions/runs/37250283674)
  también pasó. El nuevo cierre documental activa CI en la rama y en main;
  sus resultados se identifican por commit y se entregan por separado.
  CI ejecuta suites/integración, migraciones y smoke real OSRM/VROOM. No hay
  repetición manual de suites completas ni de benchmarks.
- Las mediciones previas corresponden a `88f5398`, antes de estas mitigaciones
  y del reemplazo Node. Se conservan como baseline, no como nuevas mediciones
  de latencia/RSS del runtime sustituido. No se eleva ningún límite ni se
  extrapola rendimiento de estas correcciones.

Evidencia externa: `C:\Users\erosi\Desktop\routeops-m4-2-evidence\image-security-review`.
Incluye SARIF/SBOM, identidades anteriores/finales, probes, fuentes filtradas,
logs, JUnit y hashes. El manifest general conserva los benchmarks originales.
Los snapshots completos, logs y fuentes de verificación no se versionan.

Las fichas siguientes documentan condiciones, uso y seguimiento. La ausencia de
vía es una evaluación del código/configuración observados, no una prueba formal
de todas las bibliotecas transitivas. Cambiar protocolos, parsers, permisos,
fuentes de build o exposición exige reevaluar. No hay bloqueos nuevos de
herramienta; la limpieza `blocked by policy` de 4.1 no se reintentó.

## Fichas prioritarias

Los IDs, aliases, copias y fixes por versión se conservan en el inventario JSON.

### deb:curl

- **Condición:** Proxy SOCKS5h/host largo, push HTTP/2, SMB o reutilización de cookies/Host con el mismo handle.
- **Presencia/uso/evidencia:** curl sólo sondea /health por HTTP local; VROOM no enlaza libcurl. Debian bookworm 7.88.1-10+deb12u14 incorpora los fixes antiguos (u3/u4/u6); no asumir la distribución trixie del os-release.
- **Corrección/mitigación/pendiente:** CVE-2023-38545, 38039 y 2024-2398 corregidos por distribución. Avisos nuevos de reutilización permanecen; no se usa esa vía.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2023-38039); todos los IDs y fuentes individuales están en el inventario.

### deb:dpkg

- **Condición:** dpkg-deb descomprime .deb zstd malicioso o extracción de control con permisos adversarios.
- **Presencia/uso/evidencia:** Herramienta de construcción/administración, no parser CSV/XLSX ni ejecución del servidor. No apt/dpkg invocado desde requests.
- **Corrección/mitigación/pendiente:** Fixes oficiales disponibles; instalado permanece, no corregido. Fuentes de build fijadas; no aceptar .deb del usuario.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-2219); todos los IDs y fuentes individuales están en el inventario.

### deb:expat

- **Condición:** UTF-16 mal formado: high surrogate no seguido por low surrogate, aceptado/sustituido por Expat <2.8.5.
- **Presencia/uso/evidencia:** DB Expat 2.8.3 vía bibliotecas GDAL, sin carga XML/raster por RouteOps. Backend Python vendorizado 2.8.2 sí procesa XLSX: vía aplicable detectada aunque Scout no la catalogó como paquete Expat del backend.
- **Corrección/mitigación/pendiente:** Mitigación aplicada en metadata y streaming: decoder Unicode estricto antes de Expat, incluyendo pares partidos y EOF. Expat sigue sin parche binario; 2.8.5 upstream / backport bookworm disponible. Mantener seguimiento.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-93990); todos los IDs y fuentes individuales están en el inventario.

### deb:gcc-12

- **Condición:** GNU pb_ds binary_heap::erase_if; operator new alineado con tamaño cercano al overflow.
- **Presencia/uso/evidencia:** libstdc++ runtime presente. Búsqueda VROOM/OSRM no halló __gnu_pbds; std::erase_if en extractor OSRM es otra API. JSON/filas/celdas/matrices limitados antes de solver. No es prueba de todas las bibliotecas o compilaciones.
- **Corrección/mitigación/pendiente:** Upstream tiene cambios, Debian snapshot sin fix en parte del grupo. Un bug de plantilla requiere recompilar el consumidor afectado, no sólo cambiar libstdc++. Pendiente sin vía demostrada.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-102010); todos los IDs y fuentes individuales están en el inventario.

### deb:gcc-14

- **Condición:** GNU pb_ds binary_heap::erase_if; operator new alineado con tamaño cercano al overflow.
- **Presencia/uso/evidencia:** libstdc++ en OSRM/VROOM/PostGIS; misma comprobación de fuentes y límites que gcc-12. No se ejecutan plugins/compilación de código de usuario.
- **Corrección/mitigación/pendiente:** Mismos pendientes de gcc-12; no atribuir los límites de importación a capacidad/seguridad universal del código nativo.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-102010); todos los IDs y fuentes individuales están en el inventario.

### deb:glibc

- **Condición:** iconv IBM1390/1399; reverse DNS gethostbyaddr con respuesta maliciosa; scanf %mc ancho >1024; ungetwc en codificación no Unicode.
- **Presencia/uso/evidencia:** VROOM usa 2.41-12+deb13u2, DB/OSRM u3. Entradas UTF-8/JSON, destinos internos; log_hostname=off comprobado en PostgreSQL. Búsqueda en tags VROOM/OSRM no encontró esas llamadas; no cubre todas sus dependencias.
- **Corrección/mitigación/pendiente:** u3/u4 disponibles según CVE. VROOM no incorpora u3/u4; quedan pendientes. Ausencia de vía observada no demuestra ausencia absoluta.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-4046); todos los IDs y fuentes individuales están en el inventario.

### deb:gnupg2

- **Condición:** gpg procesa armor/clave mal formada, buffer en armor_filter.
- **Presencia/uso/evidencia:** Herramienta de instalación, no importación de claves/PGP ni descifrado de originales en runtime.
- **Corrección/mitigación/pendiente:** Fix bookworm 2.2.40-1.1+deb12u2 ya instalado. Se verificó el backport, no se actualizó ni suprimió el aviso.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2025-68973); todos los IDs y fuentes individuales están en el inventario.

### deb:gnutls28

- **Condición:** DTLS, certificados SAN, PSK/PKCS11 u otras APIs específicas ante peer adversario.
- **Presencia/uso/evidencia:** Presente en VROOM por herramientas/dependencias; ldd del solver no enlaza GnuTLS. No DTLS ni PKCS11 en configuración; health curl HTTP.
- **Corrección/mitigación/pendiente:** Versiones/backports por CVE documentados; no actualización global, avisos residuales.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-42010); todos los IDs y fuentes individuales están en el inventario.

### deb:krb5

- **Condición:** GSS/Kerberos o KDC ante tickets/tokens/protocolo malicioso.
- **Presencia/uso/evidencia:** Bibliotecas presentes; no realm/KDC ni autenticación GSS configurados. PostgreSQL local usa credencial SQL, no SSO.
- **Corrección/mitigación/pendiente:** Fixes bookworm ya incluidos para los avisos antiguos según dpkg; no inferir trixie por os-release VROOM. Conservar soporte potencial como seguimiento.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2025-24528); todos los IDs y fuentes individuales están en el inventario.

### deb:libde265

- **Condición:** Bitstream H.265/PPS NAL mal formado enviado al decodificador.
- **Presencia/uso/evidencia:** Dependencia de imagen DB, no decodificación de video ni raster expuesto.
- **Corrección/mitigación/pendiente:** Fix upstream 1.0.17/paquetes Debian posteriores; installed 1.0.15 sigue inventariado, no corregido por esta entrega.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-33164); todos los IDs y fuentes individuales están en el inventario.

### deb:libheif

- **Condición:** HEIF/AVIF comprimido, tiles/planos incompatibles, secuencias o grafos que disparan memoria/loops.
- **Presencia/uso/evidencia:** Dependencia de GDAL/PostGIS, no endpoints de imagen/raster. CSV/XLSX se validan como contratos, no decodifican HEIF.
- **Corrección/mitigación/pendiente:** Upstream 1.23.2 ofrece fixes; paquete 1.19.8 permanece. Sin vía observada, seguimiento obligatorio antes de soporte raster.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-84444); todos los IDs y fuentes individuales están en el inventario.

### deb:libssh2

- **Condición:** Servidor SSH/SFTP malicioso en sesión autenticada, respuestas STATUS/NAME a OPEN/READLINK/REALPATH.
- **Presencia/uso/evidencia:** Presente DB/VROOM por dependencias; SolverGateway HTTP y originales locales. No SFTP ni claves/servidores SSH configurados por RouteOps.
- **Corrección/mitigación/pendiente:** Commits/fixes oficiales disponibles; comparaciones por lineage en inventario. Pendiente sin vía observada.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2025-15661); todos los IDs y fuentes individuales están en el inventario.

### deb:libtasn1-6

- **Condición:** asn1_expand_octet_string con objeto ASN.1 adversario.
- **Presencia/uso/evidencia:** Dependencia VROOM; sin importación ASN.1/certificados y solver/health HTTP.
- **Corrección/mitigación/pendiente:** Upstream 4.21 y backports según snapshot; no se considera corregido sólo por no uso.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2025-13151); todos los IDs y fuentes individuales están en el inventario.

### deb:libxml2

- **Condición:** DTD/xmlSnprintfElements o Python bindings SAX attributeDecl con DTD enumerado.
- **Presencia/uso/evidencia:** DB libxml2 realmente 2.9.14 con backports (versión 2.12.7+dfsg+really2.9.14...). Sin bindings Python; XLSX usa Expat y rechaza DTD. API no acepta SQL/XML ni raster; geometrías GeoJSON/PostGIS.
- **Corrección/mitigación/pendiente:** Parches upstream disponibles; avisos del binario instalado pendientes. No confundir versión really...; revisar si se añade XML/raster/SQL arbitrario.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-86140); todos los IDs y fuentes individuales están en el inventario.

### deb:nghttp2

- **Condición:** Frames HTTP/2 malformados tras terminar una sesión.
- **Presencia/uso/evidencia:** Biblioteca VROOM presente; servidores y OSRM usan HTTP/1.1; no client HTTP/2 al usuario. Node tiene su propia copia (1.70.0 final).
- **Corrección/mitigación/pendiente:** Upstream 1.68.1 ofrece fix; dpkg legado sigue inventariado, sin vía observada.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-27135); todos los IDs y fuentes individuales están en el inventario.

### deb:openldap

- **Condición:** BER/LDAP adversario recibido por biblioteca.
- **Presencia/uso/evidencia:** Dependencia curl/VROOM; no URI LDAP, directorio ni auth LDAP en RouteOps.
- **Corrección/mitigación/pendiente:** Fix oficial y estado distro por advisory; no actualizado, vía no observada.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2023-2953); todos los IDs y fuentes individuales están en el inventario.

### deb:openssl

- **Condición:** DTLS; CMS/CMP/PKCS12/TS de origen adversario; llamadas especiales de EVP_Cipher/SSL_free_buffers; negociación/certificados TLS maliciosos según CVE.
- **Presencia/uso/evidencia:** Runtime Python SSL 3.0.20, VROOM nativo 3.0.18, DB 3.5.6; OSRM-routed no enlaza SSL. Conexiones solver HTTP/TCP; DB ssl=off. psycopg usa libcrypto vendorizado 3.5.8 y Node final 3.5.8, no la biblioteca dpkg. No endpoints CMS/DTLS/certificados/importaciones criptográficas.
- **Corrección/mitigación/pendiente:** Fixes de distribución disponibles para parte del grupo, no instalados globalmente. Se conserva cada comparación/condición. No convertir ausencia de uso en parche instalado; revisar al habilitar TLS o nuevos parsers. Ocho avisos antiguos en VROOM, incluido el crítico CMS CVE-2025-15467, ya tienen backport instalado: 3.0.18-1~deb12u2. Los avisos posteriores no heredan ese estado.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-54874); todos los IDs y fuentes individuales están en el inventario.

### deb:pam

- **Condición:** pam_namespace usado con rutas/symlinks manipulados por usuario local.
- **Presencia/uso/evidencia:** VROOM contiene PAM; no login/PAM namespace en servidor npm ni solver.
- **Corrección/mitigación/pendiente:** Fix bookworm 1.5.2-6+deb12u2 ya instalado; aviso del scanner por asignación trixie no acredita vulnerabilidad de esa copia. No se actualizó en esta continuación.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2025-6020); todos los IDs y fuentes individuales están en el inventario.

### deb:pcre2

- **Condición:** Regex controlada por atacante, DFA/JIT y determinadas APIs/liberación de contextos.
- **Presencia/uso/evidencia:** Python usa re, Node RegExp y PostgreSQL motor POSIX propio. OSRM/VROOM ldd sin PCRE2. Contratos no aceptan regex de usuario; biblioteca existe en varias imágenes.
- **Corrección/mitigación/pendiente:** Bookworm u2 y trixie u2/u3 ofrecen fixes; versiones instaladas varían, no todas corregidas. Revaluar si se agrega entrada regex/uso JIT.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-103111); todos los IDs y fuentes individuales están en el inventario.

### deb:perl

- **Condición:** Evaluación de globs/plantillas, regex adversarias, TAR/ZIP/POD o CPAN TLS en intérprete Perl.
- **Presencia/uso/evidencia:** Servidor Python/Node/C++, no invoca Perl para CSV/XLSX ni exportación; preparado OSM sin Perl. perl-base instalado; módulos opcionales del source no implican que estén todos incluidos.
- **Corrección/mitigación/pendiente:** Backports antiguos comprobados; avisos nuevos pendientes sin vía de importación/runtime observada. No se eliminó Perl ni se marcaron todos corregidos.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-48962); todos los IDs y fuentes individuales están en el inventario.

### deb:sqlite3

- **Condición:** SQL/estructura de consultas que activa corrupción o crash en el motor SQLite.
- **Presencia/uso/evidencia:** DB/OSRM contienen libsqlite3; RouteOps usa PostgreSQL, no importa SQLite ni ofrece SQL libre. OSRM-routed ldd sin SQLite. SQLite de Node es otra copia vendorizada 3.53.4.
- **Corrección/mitigación/pendiente:** Fixes upstream según advisory; paquetes señalados no actualizados. Ausencia de vía observada; no se declara fix instalado.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-11822); todos los IDs y fuentes individuales están en el inventario.

### deb:systemd

- **Condición:** Validador DNSSEC systemd-resolved con DNSKEY/RRSIG/NSEC3 patológico.
- **Presencia/uso/evidencia:** Bibliotecas/paquetes en VROOM no implican daemon resolved en ejecución; contenedor ejecuta npm/node y solver, DNS Compose. No DNSSEC validador implementado por RouteOps.
- **Corrección/mitigación/pendiente:** Conservar avisos del paquete, no suprimir; DNS externo/resolved del host queda fuera de esta imagen y debe evaluarse aparte.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2023-50387); todos los IDs y fuentes individuales están en el inventario.

### deb:util-linux

- **Condición:** mount/nsenter privilegiados, fstab autorizado, hooks/idmap/symlinks controlados localmente.
- **Presencia/uso/evidencia:** Apps no root, cap_drop ALL y no-new-privileges. No servicios que acepten mount/fstab/nsenter del usuario. Herramientas permanecen.
- **Corrección/mitigación/pendiente:** Parches upstream disponibles; algunos backports aún pendientes. No equivaler mitigación de privilegios a fix de paquete.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-78409); todos los IDs y fuentes individuales están en el inventario.

### deb:xz-utils

- **Condición:** lzma_stream_decoder_mt con .xz malicioso.
- **Presencia/uso/evidencia:** Presente en VROOM; runtime no recibe .xz. OSM confiable gzip con checksum; XLSX ZIP no utiliza liblzma mt.
- **Corrección/mitigación/pendiente:** Fix de rama 5.4/upstream 5.8.1 disponible; paquete no se cambió. Pendiente sin entrada observada.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2025-31115); todos los IDs y fuentes individuales están en el inventario.

### deb:zlib

- **Condición:** MiniZip zipOpenNewFileInZip4_64 con metadatos desmesurados; gzwrite/gzprintf con condiciones de entero/stream no bloqueante.
- **Presencia/uso/evidencia:** Bookworm zlib1g no compila contrib/minizip (nota oficial Debian); minizip trixie de DB sí existe y contiene fix. XLSX usa zipfile con expansión limitada; Node usa zlib vendorizado, no gzprintf. Logs VROOM a fichero/gzip, sin API que exponga esos parámetros.
- **Corrección/mitigación/pendiente:** MiniZip: no afectado en esos binarios bookworm; fix instalado en trixie. CVE-2026-85091 queda sin fix, vía no observada; no declarar zlib completo corregido.
- **Fuente:** [advisory oficial y/o distribución](https://security-tracker.debian.org/tracker/CVE-2026-85091); todos los IDs y fuentes individuales están en el inventario.

### golang:stdlib

- **Condición:** Funciones específicas x509/TLS/HTTP2/mail/URL/JSON/os.Root/DNS; algunas requieren Windows.
- **Presencia/uso/evidencia:** Binarios identificados: tsc TypeScript 7.0.2 (Go 1.26.4), sólo compilación de fuentes confiables; gosu 1.19 (Go 1.24.6) cambia usuario antes de exec postgres, no sirve HTTP/TLS. CVEs Windows no aplican al binario linux/amd64. La presencia de stdlib no demuestra que cada función esté enlazada o llamada.
- **Corrección/mitigación/pendiente:** Fixes Go enumerados por advisory oficial; binarios no recompilados. No uso de esas entradas adversarias observado; incertidumbre de reachability transitiva conservada. No llamar corregidos a todos.
- **Fuente:** [advisory oficial y/o distribución](https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/2026/33xxx/CVE-2026-33818.json); todos los IDs y fuentes individuales están en el inventario.

### npm:brace-expansion

- **Condición:** Expansión de patrones/braces adversarios, recursión o producto explosivo.
- **Presencia/uso/evidencia:** npm y tooling de frontend/VROOM; npm start usa script fijo, no patrones de nombres de archivos recibidos.
- **Corrección/mitigación/pendiente:** Fixes de majors por advisory; no invocado con patrón adversario en runtime observado. Paquetes no actualizados.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-102276); todos los IDs y fuentes individuales están en el inventario.

### npm:braces

- **Condición:** Patrón de glob/braces adversario produce memoria/CPU/stack.
- **Presencia/uso/evidencia:** VROOM tooling, no campos de dataset usados como patrones glob.
- **Corrección/mitigación/pendiente:** Fixes disponibles; copias pendientes, sin vía runtime observada.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2024-4068); todos los IDs y fuentes individuales están en el inventario.

### npm:cross-spawn

- **Condición:** Entrada muy larga/especial en sanitización regex de argumentos del spawn.
- **Presencia/uso/evidencia:** npm tooling, no field controlado por imports que se pase a este módulo; solver usa spawn nativo con argumentos controlados.
- **Corrección/mitigación/pendiente:** Fix7.0.5 disponible; copia npm VROOM7.0.3 permanece, no corregida.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2024-21538); todos los IDs y fuentes individuales están en el inventario.

### npm:execa

- **Condición:** preferLocal ejecuta binario local controlado; requisito de árbol de proyecto/binary lookup adversario.
- **Presencia/uso/evidencia:** VROOM tooling/lint-staged, no importado por server. Solver usa child_process.spawn con path/config controlados; argumentos externos override sólo numéricos. No se toma input como comando execa.
- **Corrección/mitigación/pendiente:** GMS-2020-2 sin registro OSV/CNA recuperable: incertidumbre de fuente explícita. No supresión ni declaración corregida; no vía runtime observada.
- **Fuente:** Scout; lookup OSV/CNA no recuperado, incertidumbre conservada.

### npm:fast-uri

- **Condición:** Canonicalización de URI usada como frontera de confianza/SSRF o regex/parsing de inputs especiales.
- **Presencia/uso/evidencia:** VROOM herramientas de schemas/linting, no servidor. Guard usa WHATWG URL y whitelist de hostname; routingServers fija osrm y no admite URL del archivo.
- **Corrección/mitigación/pendiente:** Fixes listados por advisory; sin upgrade masivo. No tratar ausencia del flujo como fix.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-13676); todos los IDs y fuentes individuales están en el inventario.

### npm:glob

- **Condición:** CLI glob -c/--cmd ejecuta shell con nombres de archivo adversarios.
- **Presencia/uso/evidencia:** npm tooling; scripts RouteOps no invocan glob -c con uploads/nombres de originales.
- **Corrección/mitigación/pendiente:** Fix oficial disponible; copia pendiente sin ejecución observada.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2025-64756); todos los IDs y fuentes individuales están en el inventario.

### npm:http-cache-semantics

- **Condición:** Cache HTTP compartida multiusuario, max-stale y entrada security-zeroed expone sesión ajena.
- **Presencia/uso/evidencia:** npm dependency, no cache compartida de datos RouteOps implementada por esta biblioteca. Aplicación local/un usuario; npm no recibe requests HTTP del servidor.
- **Corrección/mitigación/pendiente:** Aviso obtenido también de CNA; fix upstream incierto según fuente. Pendiente, no corregido.
- **Fuente:** [advisory oficial y/o distribución](https://raw.githubusercontent.com/CVEProject/cvelistV5/main/cves/2026/93xxx/CVE-2026-93748.json); todos los IDs y fuentes individuales están en el inventario.

### npm:ip-address

- **Condición:** Lógica de permiso/SSRF confía en decimal con ceros mientras stack interpreta octal.
- **Presencia/uso/evidencia:** npm dependency; destinos de gateway/config fijos, no checker SSRF con esta API. Guard WHATWG+whitelist.
- **Corrección/mitigación/pendiente:** Fix oficial por advisory; paquetes instalados permanecen.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-69192); todos los IDs y fuentes individuales están en el inventario.

### npm:js-yaml

- **Condición:** YAML con merges/alias encadenados o manipulaciones de tipo/clave del advisory.
- **Presencia/uso/evidencia:** Runtime 4.1.1 carga únicamente /conf/config.yml read-only controlado por repo; 3.14.2 de tooling. Ningún endpoint acepta YAML.
- **Corrección/mitigación/pendiente:** Fixes upstream por advisory; ambas copias se mantienen. Revalidar antes de aceptar YAML externo.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-59869); todos los IDs y fuentes individuales están en el inventario.

### npm:lodash

- **Condición:** _.template con imports/variable/keys adversarios lleva a Function/evaluación.
- **Presencia/uso/evidencia:** VROOM dev dependencies, server no llama _.template ni evalúa textos de importación.
- **Corrección/mitigación/pendiente:** Fix oficial pendiente en copia instalada; entrada y llamada no observadas.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-4800); todos los IDs y fuentes individuales están en el inventario.

### npm:minimatch

- **Condición:** Glob con ** repetidos o segmentos adversarios provoca backtracking.
- **Presencia/uso/evidencia:** npm/linting; no matching glob configurado por usuario ni importaciones interpretadas como patrones.
- **Corrección/mitigación/pendiente:** Fixes específicos en advisories, copias pendientes.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-27903); todos los IDs y fuentes individuales están en el inventario.

### npm:pacote

- **Condición:** Spec git/rawSpec adversaria dispara addGitSha y CPU.
- **Presencia/uso/evidencia:** Instalación npm, no nuevas dependencias desde requests/archivos. npm start script fijo.
- **Corrección/mitigación/pendiente:** Fix oficial/estado según advisory; pendiente en tooling, no exposición runtime observada.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-9496); todos los IDs y fuentes individuales están en el inventario.

### npm:path-to-regexp

- **Condición:** Patrón de ruta con >=3 parámetros en un segmento separados por no-punto; URL dispara backtracking.
- **Presencia/uso/evidencia:** Copia runtime 0.1.12 en Express VROOM. Source index sólo app.post(baseurl) y get(baseurl+health), baseurl fija /; sin patrones paramétricos. Guard limita path 2048 y rechaza query antes de Express.
- **Corrección/mitigación/pendiente:** 0.1.13 corrige; paquete aún 0.1.12, condición de patrón ausente en configuración revisada. No falso claim de actualización.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-4867); todos los IDs y fuentes individuales están en el inventario.

### npm:property-expr

- **Condición:** setter escribe propiedad/prototipo con expresión controlada.
- **Presencia/uso/evidencia:** VROOM tooling, no uso server ni setters genéricos desde importaciones JSON.
- **Corrección/mitigación/pendiente:** Fix 2.0.3 disponible, copia1.5.1 permanece; no corregida.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2020-7707); todos los IDs y fuentes individuales están en el inventario.

### npm:sigstore

- **Condición:** Consumidor usa certificateOIDs creyendo que el verificador lo exige.
- **Presencia/uso/evidencia:** npm vendor, no verificación/certificados de archivos RouteOps ni uso de esa opción.
- **Corrección/mitigación/pendiente:** Fix oficial por advisory; sigue inventariado.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-48815); todos los IDs y fuentes individuales están en el inventario.

### npm:simple-git

- **Condición:** Argumentos git clone/fetch/upload-pack/submódulo controlados por atacante.
- **Presencia/uso/evidencia:** VROOM dev tree (lint-staged); árbol --omit=dev y source server no lo invocan. No operaciones git desde JSON. Resolvable por estar instalado no implica carga runtime.
- **Corrección/mitigación/pendiente:** Upgrades selectivos de tooling pendientes; no se elimina ni declara corregido. Git del workflow no usa esta biblioteca.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2022-24066); todos los IDs y fuentes individuales están en el inventario.

### npm:tar

- **Condición:** Extraer/listar TAR malicioso, selección de miembros/path traversal/symlink/hardlink.
- **Presencia/uso/evidencia:** Copia npm (7.5.19 frontend y6.2.1 VROOM); no parser de originales ni exportaciones, que son ZIP/CSV/XLSX. npm start no instala/descomprime paquetes en requests.
- **Corrección/mitigación/pendiente:** Fixes npm tar disponibles, sin actualización silenciosa del npm vendor. Riesgo de build/install separado: sólo fuentes/lock confiables, no importar TAR.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-73566); todos los IDs y fuentes individuales están en el inventario.

### npm:undici

- **Condición:** Cliente WebSocket a peer adversario responde Sec-WebSocket-Protocol no solicitado y hace crash.
- **Presencia/uso/evidencia:** Copia npm6.27.0, diferente de Node builtin7.29.1. Vite ofrece HMR WebSocket servidor, no cliente hacia URLs adversarias mediante esa copia npm.
- **Corrección/mitigación/pendiente:** Fix advisory disponible, npm vendor no cambiado. No igualar builtin y npm; vía no observada.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-19534); todos los IDs y fuentes individuales están en el inventario.

### pypi:msgpack

- **Condición:** Reutilizar Unpacker nativo tras excepción, SEGV/lectura fuera de límites.
- **Presencia/uso/evidencia:** pip vendor1.1.2 usa fallback Python (probe), no extensión C; API/originales no MessagePack. Scanner vio BOM vendor, no flujo runtime.
- **Corrección/mitigación/pendiente:** 1.2.1 ofrece fix. No upgrade del vendor; ausencia del camino C y de uso runtime documentada.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-57585); todos los IDs y fuentes individuales están en el inventario.

### pypi:setuptools

- **Condición:** PackageIndex descarga URL con path/fragmento adversario a ruta fuera del directorio.
- **Presencia/uso/evidencia:** pip vendor70.3.0, no setuptools de aplicación ni PackageIndex usado por CSV/XLSX. Instala lock por pip, no URLs del usuario.
- **Corrección/mitigación/pendiente:** 78.1.1 corrige esa API, pero vendor permanece. Riesgo de instalación separado, no marcado corregido.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2025-47273); todos los IDs y fuentes individuales están en el inventario.

### pypi:urllib3

- **Condición:** HTTPS proxy/target con contextos distintos o modos mTLS donde configuración se confunde.
- **Presencia/uso/evidencia:** pip vendor2.7.0; gateway HTTPX/httpcore, no urllib3. No proxy HTTPS/mTLS configurado por usuario. No equivale versión pip26.2.1 a todas las vendors corregidas.
- **Corrección/mitigación/pendiente:** Fix2.8.0 según advisories; copia permanece, sin vía runtime observada. Revisar build con proxy corporativo.
- **Fuente:** [advisory oficial y/o distribución](https://api.osv.dev/v1/vulns/CVE-2026-97687); todos los IDs y fuentes individuales están en el inventario.
