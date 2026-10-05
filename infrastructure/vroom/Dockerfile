FROM node:24.21.0-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS node_runtime

FROM ghcr.io/vroom-project/vroom-docker:v1.15.0@sha256:247d5683d6745c755d718a156d16b16aac80baccc276a003a68b986c13883b08

# Keep the pinned solver, wrapper dependencies, router and dataset unchanged.
# Guard the wrapper before Express parses a browser-controlled request.
# Replace only the unsupported Node 20 executable; npm and wrapper dependencies
# remain inventoried, including their outstanding advisories.
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
# Retain the matching runtime's MIT and bundled third-party notices. The base
# image still carries its original Node 20 notices separately.
COPY --from=node_runtime /usr/local/LICENSE /routeops/licenses/node-24-LICENSE
COPY request-guard.cjs /routeops/request-guard.cjs
ENV NODE_OPTIONS="--require=/routeops/request-guard.cjs"
