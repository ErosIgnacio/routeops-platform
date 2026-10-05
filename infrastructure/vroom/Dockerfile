FROM ghcr.io/vroom-project/vroom-docker:v1.15.0@sha256:247d5683d6745c755d718a156d16b16aac80baccc276a003a68b986c13883b08

# Keep the pinned solver, wrapper dependencies, router and dataset unchanged.
# Guard the wrapper before Express parses a browser-controlled request.
COPY request-guard.cjs /routeops/request-guard.cjs
ENV NODE_OPTIONS="--require=/routeops/request-guard.cjs"
