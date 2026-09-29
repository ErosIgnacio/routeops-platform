# VROOM and OSRM version assessment

Checked against upstream project sources on **2026-09-24**. The statements
below are documentation-level compatibility findings; runtime compatibility is
an explicit Milestone 1 smoke test and has not yet been claimed.

## Recommended pins

| Component | Current stable/upstream version | Proposed image pin |
|---|---:|---|
| VROOM | `1.15.0` | `ghcr.io/vroom-project/vroom-docker:v1.15.0@sha256:247d5683d6745c755d718a156d16b16aac80baccc276a003a68b986c13883b08` |
| vroom-express | `0.12.0` | Bundled in the VROOM image above |
| OSRM backend | `26.9.0` | `ghcr.io/project-osrm/osrm-backend:26.9.0-debian@sha256:8a1b1bc938412f15f9b5b32d794c4ec6bf4a85dfbbabfa0a014b70b187edb53b` |

Use both the readable tag and manifest digest in Compose. Do not use `latest`:
upstream defines the OSRM `latest` image as a moving build, and VROOM also
publishes a moving `latest` independently from its stable tag.

## Evidence

- The official [VROOM releases](https://github.com/VROOM-Project/vroom/releases)
  and [usage page](https://github.com/VROOM-Project/vroom/wiki/Usage) identify
  `1.15.0` as the latest stable release.
- The official [vroom-express releases](https://github.com/VROOM-Project/vroom-express/releases)
  identify `0.12.0` as latest.
- The official [vroom-docker README](https://github.com/VROOM-Project/vroom-docker)
  documents the `v1.15.0` image and shows that it is built from VROOM `v1.15.0`
  plus vroom-express `v0.12.0`.
- The official [VROOM container registry](https://github.com/VROOM-Project/vroom-docker/pkgs/container/vroom-docker)
  publishes the multi-architecture `v1.15.0` manifest and digest above.
- The official [OSRM releases](https://github.com/Project-OSRM/osrm-backend/releases)
  identify `26.9.0`, released 2026-09-01, as latest.
- The official [OSRM container registry](https://github.com/Project-OSRM/osrm-backend/pkgs/container/osrm-backend)
  publishes multi-architecture `26.9.0-debian` and the digest above.
- OSRM's official [release policy](https://github.com/Project-OSRM/osrm-backend/blob/master/docs/releasing.md)
  uses date-based versions from 2026 and states that datasets are compatible
  within a month's patch releases but must be rebuilt between monthly releases.

## Compatibility assessment

1. VROOM uses OSRM as its default routing backend and its documented HTTP path
   is compatible with OSRM's `v1` route/table API.
2. VROOM `1.15.0` explicitly updated its optional in-process `LibosrmWrapper`
   for the breaking OSRM v6 change. RouteOps uses separate containers and the
   HTTP integration, not the `libosrm` embedding, but this is further evidence
   that current VROOM is aligned with post-v6 OSRM.
3. The official VROOM image is the authoritative pairing of VROOM `1.15.0` and
   vroom-express `0.12.0`; RouteOps should consume that image instead of
   maintaining an unnecessary fork.
4. Both proposed manifests expose `linux/amd64` and `linux/arm64`. This covers
   ordinary Linux hosts and Docker Desktop/WSL2; Windows runs Linux containers.
5. OSRM preprocessing (`extract`, `partition`, `customize`) and serving must use
   the exact same pinned image. A version bump requires rebuilding all `.osrm*`
   artifacts and updating the recorded extract checksum.
6. VROOM's default OSRM table/route size limits must be reconciled with
   `osrm-routed --max-table-size` and `--max-viaroute-size`. The values will be
   intentionally bounded from the application workload, not set to unlimited.

## Remaining verification for Milestone 1

- Pull both exact manifests on the target architecture.
- Build a small synthetic Santiago OSRM MLD dataset using the pinned image.
- Verify OSRM `route` and `table` health calls.
- Configure vroom-express to address the Compose service name `osrm`, not
  `localhost`.
- Assert the VROOM health endpoint and solve a two-center fixture with geometry,
  time windows, skills, and three capacity dimensions.
- Record `vroom --version`, OSRM version/image digest, HTTP payload hashes, and
  map dataset checksum in the smoke-test artifact.

The approved bounded extract was downloaded on 2026-09-24. Its 4,145,215-byte
source and SHA-256 are recorded in `data/osrm/source-lock.json`; OSRM processing
and cross-service verification still require Docker.
