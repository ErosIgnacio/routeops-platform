import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";

import { featureBounds, routeFeatureCollection, stopFeatureCollection } from "./map-data";
import type { PlanningRun } from "./types";

export type MapResultTarget = Pick<MapLibreMap, "fitBounds" | "getSource">;

export function updateMapResult(
  map: MapResultTarget,
  run: PlanningRun | null,
  selectedVehicleId: string | null = null,
  refit = true,
): boolean {
  const routes = routeFeatureCollection(run, selectedVehicleId);
  const stops = stopFeatureCollection(run, selectedVehicleId);
  const routeSource = map.getSource("routes") as GeoJSONSource | undefined;
  const stopSource = map.getSource("stops") as GeoJSONSource | undefined;
  if (!routeSource || !stopSource) return false;

  routeSource.setData(routes);
  stopSource.setData(stops);

  if (refit) fitMapResult(map, run, selectedVehicleId);
  return true;
}

export function fitMapResult(
  map: Pick<MapLibreMap, "fitBounds">,
  run: PlanningRun | null,
  selectedVehicleId: string | null = null,
): void {
  const routes = routeFeatureCollection(run, selectedVehicleId);
  const stops = stopFeatureCollection(run, selectedVehicleId);
  const routesForBounds = selectedVehicleId
    ? {
        ...routes,
        features: routes.features.filter(
          (feature) => feature.properties.vehicle === selectedVehicleId,
        ),
      }
    : routes;
  const stopsForBounds = selectedVehicleId
    ? {
        ...stops,
        features: stops.features.filter(
          (feature) => feature.properties.vehicle === selectedVehicleId,
        ),
      }
    : stops;
  const bounds = featureBounds(routesForBounds, stopsForBounds);
  if (bounds) {
    // A small geographic box also handles a single stop without an extreme zoom.
    if (bounds[0][0] === bounds[1][0] && bounds[0][1] === bounds[1][1]) {
      bounds[0][0] -= 0.001;
      bounds[0][1] -= 0.001;
      bounds[1][0] += 0.001;
      bounds[1][1] += 0.001;
    }
    map.fitBounds(bounds, {
      padding: { top: 64, right: 64, bottom: 64, left: 64 },
      maxZoom: 15.25,
      duration: 450,
    });
  }
}
