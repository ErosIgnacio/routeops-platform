# Third-party notices

RouteOps source is licensed under [Apache-2.0](LICENSE). Builds distribute
dependency code and executable binaries. Dependencies, container contents,
map data and tiles retain their own terms; Apache-2.0 does not relicense them.

- [VROOM](https://github.com/VROOM-Project/vroom), BSD-2-Clause.
- [vroom-express](https://github.com/VROOM-Project/vroom-express),
  BSD-2-Clause.
- [OSRM backend](https://github.com/Project-OSRM/osrm-backend), BSD-2-Clause.
- [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js), BSD-3-Clause.
- [OpenStreetMap](https://www.openstreetmap.org/copyright) data, © OpenStreetMap
  contributors, available under the Open Database License (ODbL).

The development map uses OpenStreetMap raster tiles subject to the
[tile usage policy](https://operations.osmfoundation.org/policies/tiles/). This
public tile source is for low-volume local demonstration only and must be
replaced by an appropriate provider or self-hosted tiles for publication or
material traffic.

## Dependency and image inventory

[The generated inventory](docs/research/m43-license-inventory.json) records
33 installed Python distributions and 237 frontend lock entries, including
development and optional dependencies. `scripts/review/licenses.py` reproduces
it from an isolated backend image and the frontend lock. Wheel notice paths,
license expressions/classifiers and integrity values remain inspectable.
Lock presence does not prove platform installation or Vite bundle inclusion.
Metadata inventory is evidence, not a legal certification of redistribution.
Python includes the separate MPL/LGPL terms of certifi and psycopg; upstream
notices are retained. Base-image OS/vendor contents are outside this inventory:
consult the [4.2 image assessment](docs/milestone-4-2-image-security-review.md),
retained SBOMs and upstream sources before redistributing images.

- [Node 24.21.0 and third-party notices](https://github.com/nodejs/node/blob/v24.21.0/LICENSE).
  The VROOM image now retains the matching notice at
  `/routeops/licenses/node-24-LICENSE` in addition to the original upstream
  `/usr/local/LICENSE`. Its SHA-256 is
  `5888dbb9a1d2b18f2c3e6c5f6af1b39de658372b402a0577b002777f14c62ace`,
  matching the pinned frontend Node image. Solver binaries are unchanged.
- [PostgreSQL license](https://www.postgresql.org/about/licence/) and
  [PostGIS GPL](https://postgis.net/documentation/faq/gpl-license/) remain separate.
- The tracked OSM source archive has its source/date/bounds/hash and attribution
  in [the source lock](data/osrm/source-lock.json) and
  [archive metadata](infrastructure/ci/osm-archive.json). Preserve these when
  copying source data and preserve OSM attribution in captures/map displays.

Residual security advisories remain recorded separately from licensing. No
complete-container redistribution certification or public deployment is implied.
