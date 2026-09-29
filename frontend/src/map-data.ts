import type { FeatureCollection, LineString, Point } from "geojson";
import type { PlanningRun } from "./types";

export const ROUTE_COLORS = {
  "VEH-CENTRO-01": "#3338d6",
  "VEH-ORIENTE-01": "#eb1010",
} as const;

const fallbackColors = ["#5ea5ff", "#df7cff", "#ff6b78", "#8bd450"];

export function routeColorForVehicle(vehicleId: string): string {
  const configured = ROUTE_COLORS[vehicleId as keyof typeof ROUTE_COLORS];
  if (configured) return configured;

  const hash = [...vehicleId].reduce((value, character) => {
    return (value * 31 + character.charCodeAt(0)) >>> 0;
  }, 0);
  return fallbackColors[hash % fallbackColors.length]!;
}

export function routeFeatureCollection(
  run: PlanningRun | null,
  selectedVehicleId: string | null = null,
): FeatureCollection<
  LineString,
  { color: string; vehicle: string; selected: boolean; selectionActive: boolean }
> {
  return {
    type: "FeatureCollection",
    features:
      run?.result?.routes
        .filter((route) => route.geometry.length >= 2)
        .map((route) => ({
          type: "Feature",
          properties: {
            color: routeColorForVehicle(route.source_vehicle_id),
            vehicle: route.source_vehicle_id,
            selected:
              selectedVehicleId === null || selectedVehicleId === route.source_vehicle_id,
            selectionActive: selectedVehicleId !== null,
          },
          geometry: {
            type: "LineString",
            coordinates: route.geometry.map((point) => [point.longitude, point.latitude]),
          },
        })) ?? [],
  };
}

type StopProperties = {
  kind: string;
  order: string;
  sequence: number;
  color: string;
  vehicle: string;
  distributionCenter: string;
  selected: boolean;
  selectionActive: boolean;
};

export function stopFeatureCollection(
  run: PlanningRun | null,
  selectedVehicleId: string | null = null,
): FeatureCollection<Point, StopProperties> {
  const features =
    run?.result?.routes.flatMap((route) =>
      route.steps.map((step) => ({
        type: "Feature" as const,
        properties: {
          kind: step.kind,
          order: step.order_id ?? route.distribution_center_id,
          sequence: step.sequence,
          color: routeColorForVehicle(route.source_vehicle_id),
          vehicle: route.source_vehicle_id,
          distributionCenter: route.distribution_center_id,
          selected:
            selectedVehicleId === null || selectedVehicleId === route.source_vehicle_id,
          selectionActive: selectedVehicleId !== null,
        },
        geometry: {
          type: "Point" as const,
          coordinates: [step.location.longitude, step.location.latitude],
        },
      })),
    ) ?? [];
  return { type: "FeatureCollection", features };
}

export type MapBounds = [[number, number], [number, number]];

export function featureBounds(
  routes: ReturnType<typeof routeFeatureCollection>,
  stops: ReturnType<typeof stopFeatureCollection>,
): MapBounds | null {
  const coordinates = [
    ...routes.features.flatMap((feature) => feature.geometry.coordinates),
    ...stops.features.map((feature) => feature.geometry.coordinates),
  ];
  if (coordinates.length === 0) return null;

  let west = coordinates[0]![0];
  let east = west;
  let south = coordinates[0]![1];
  let north = south;
  for (const [longitude, latitude] of coordinates.slice(1)) {
    west = Math.min(west, longitude);
    east = Math.max(east, longitude);
    south = Math.min(south, latitude);
    north = Math.max(north, latitude);
  }
  return [
    [west, south],
    [east, north],
  ];
}
