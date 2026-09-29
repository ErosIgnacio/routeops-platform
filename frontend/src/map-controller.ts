import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";

import { featureBounds, routeFeatureCollection, stopFeatureCollection } from "./map-data";
import type { PlanningRun } from "./types";

export type MapResultTarget = Pick<MapLibreMap, "fitBounds" | "getSource">;

export function updateMapResult(
  map: MapResultTarget,
  run: PlanningRun | null,
  selectedVehicleId: string | null = null,
): boolean {
  const routes = routeFeatureCollection(run, selectedVehicleId);
  const stops = stopFeatureCollection(run, selectedVehicleId);
  const routeSource = map.getSource("routes") as GeoJSONSource | undefined;
  const stopSource = map.getSource("stops") as GeoJSONSource | undefined;
  if (!routeSource || !stopSource) return false;

  routeSource.setData(routes);
  stopSource.setData(stops);

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
    map.fitBounds(bounds, {
      padding: { top: 64, right: 64, bottom: 64, left: 64 },
      maxZoom: 15.25,
      duration: 450,
    });
  }
  return true;
}
