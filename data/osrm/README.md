# Santiago demo routing data

Generated `.osm`, `.osrm*`, checksum, and metadata files are intentionally not
committed. The bounding box contains only the small synthetic demonstration
area; order coordinates are fictional points and are not customer addresses.

From PowerShell:

```powershell
./infrastructure/osrm/download-osm.ps1
docker compose --profile tools run --rm osrm-prepare
```

From a POSIX shell:

```bash
./infrastructure/osrm/download-osm.sh
docker compose --profile tools run --rm osrm-prepare
```

The download is capped at 50 MiB. The source query, time, size, and checksum are
recorded beside the ignored runtime file. The reviewed fingerprint is committed
in [`source-lock.json`](source-lock.json). If the live OpenStreetMap data changes,
review the diff and update that lock intentionally.

Current approved extract: 4,145,215 bytes, SHA-256
`22230c1c093a0fe50bbb240cce3d2c36aaf34e8bfb2fe136b76405084de213c4`.

Data © OpenStreetMap contributors, ODbL 1.0.
