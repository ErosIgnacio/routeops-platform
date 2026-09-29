#!/bin/sh
set -eu

source_file="${1:-/data/santiago-demo.osm}"
base_file="${source_file%.*}"

if [ ! -f "$source_file" ]; then
  echo "Missing $source_file. Run the documented OSM download script first." >&2
  exit 2
fi

if [ -f "${base_file}.osrm.mldgr" ]; then
  echo "OSRM data already exists for ${base_file}; preparation skipped."
  exit 0
fi

osrm-extract -p /opt/car.lua "$source_file"
osrm-partition "${base_file}.osrm"
osrm-customize "${base_file}.osrm"
sha256sum "$source_file" > "${source_file}.sha256"
