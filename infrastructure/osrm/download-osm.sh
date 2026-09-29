#!/usr/bin/env sh
set -eu

destination="${1:-data/osrm/santiago-demo.osm}"
maximum_bytes="${ROUTEOPS_OSM_MAX_BYTES:-52428800}"
query='[out:xml][timeout:120];(way["highway"](-33.4550,-70.6750,-33.4250,-70.6250);>;);out body qt;'
encoded_query=$(printf '%s' "$query" | sed 's/ /%20/g; s/\[/%5B/g; s/\]/%5D/g; s/"/%22/g; s/;/%3B/g; s/(/%28/g; s/)/%29/g; s/:/%3A/g; s/,/%2C/g; s/>/%3E/g')
temporary="${destination}.download"

case "$destination" in
  data/osrm/*) ;;
  *) echo "Destination must stay inside data/osrm" >&2; exit 2 ;;
esac

mkdir -p "$(dirname "$destination")"
curl --fail --location --silent --show-error \
  --user-agent 'RouteOps-portfolio/0.1' \
  "https://overpass-api.de/api/interpreter?data=${encoded_query}" \
  --output "$temporary" || \
curl --fail --location --silent --show-error \
  --user-agent 'RouteOps-portfolio/0.1' \
  "https://overpass.kumi.systems/api/interpreter?data=${encoded_query}" \
  --output "$temporary" || \
curl --fail --location --silent --show-error \
  --user-agent 'RouteOps-portfolio/0.1' \
  "https://overpass.private.coffee/api/interpreter?data=${encoded_query}" \
  --output "$temporary"

bytes=$(wc -c < "$temporary" | tr -d ' ')
if [ "$bytes" -gt "$maximum_bytes" ]; then
  rm -f "$temporary"
  echo "Downloaded extract is $bytes bytes, above the $maximum_bytes byte limit." >&2
  exit 3
fi

mv "$temporary" "$destination"
sha256sum "$destination" > "${destination}.sha256"
hash=$(sha256sum "$destination" | cut -d ' ' -f 1)
downloaded_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
escaped_query=$(printf '%s' "$query" | sed 's/\\/\\\\/g; s/"/\\"/g')
printf '{\n  "source": "OpenStreetMap via Overpass API",\n  "query": "%s",\n  "downloaded_at_utc": "%s",\n  "sha256": "%s",\n  "bytes": %s,\n  "license": "ODbL-1.0"\n}\n' \
  "$escaped_query" "$downloaded_at" "$hash" "$bytes" > "${destination}.meta.json"
echo "Downloaded $bytes bytes to $destination"
