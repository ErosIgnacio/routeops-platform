import { describe, expect, it } from "vitest";

import {
  ROUTE_COLORS,
  featureBounds,
  routeColorForVehicle,
  routeFeatureCollection,
  stopFeatureCollection,
} from "./map-data";
import type { PlanningRun } from "./types";

describe("routeFeatureCollection", () => {
  it("maps domain coordinates to GeoJSON longitude-latitude order", () => {
    const run = {
      result: {
        routes: [
          {
            source_vehicle_id: "VEH-CENTRO-01",
            distribution_center_id: "DC-CENTRO",
            steps: [],
            geometry: [
              { latitude: -33.44, longitude: -70.66 },
              { latitude: -33.43, longitude: -70.65 },
              { latitude: -33.42, longitude: -70.64 },
              { latitude: -33.41, longitude: -70.63 },
            ],
          },
        ],
      },
    } as unknown as PlanningRun;

    expect(routeFeatureCollection(run).features[0]?.geometry.coordinates).toEqual([
      [-70.66, -33.44],
      [-70.65, -33.43],
      [-70.64, -33.42],
      [-70.63, -33.41],
    ]);
  });

  it("keeps vehicle colors stable regardless of route order", () => {
    expect(routeColorForVehicle("VEH-CENTRO-01")).toBe(ROUTE_COLORS["VEH-CENTRO-01"]);
    expect(routeColorForVehicle("VEH-ORIENTE-01")).toBe(ROUTE_COLORS["VEH-ORIENTE-01"]);

    const run = {
      result: {
        routes: [
          {
            source_vehicle_id: "VEH-ORIENTE-01",
            distribution_center_id: "DC-ORIENTE",
            steps: [],
            geometry: [
              { latitude: -33.43, longitude: -70.64 },
              { latitude: -33.42, longitude: -70.63 },
            ],
          },
          {
            source_vehicle_id: "VEH-CENTRO-01",
            distribution_center_id: "DC-CENTRO",
            steps: [],
            geometry: [
              { latitude: -33.45, longitude: -70.67 },
              { latitude: -33.44, longitude: -70.66 },
            ],
          },
        ],
      },
    } as unknown as PlanningRun;

    const colors = Object.fromEntries(
      routeFeatureCollection(run).features.map((feature) => [
        feature.properties.vehicle,
        feature.properties.color,
      ]),
    );
    expect(colors).toEqual({
      "VEH-ORIENTE-01": "#eb1010",
      "VEH-CENTRO-01": "#3338d6",
    });

    const selected = routeFeatureCollection(run, "VEH-CENTRO-01").features;
    expect(selected.map((feature) => [feature.properties.vehicle, feature.properties.selected])).toEqual([
      ["VEH-ORIENTE-01", false],
      ["VEH-CENTRO-01", true],
    ]);
  });

  it("fits all route geometry and stop coordinates", () => {
    const run = {
      result: {
        routes: [
          {
            source_vehicle_id: "VEH-CENTRO-01",
            distribution_center_id: "DC-CENTRO",
            geometry: [
              { latitude: -33.46, longitude: -70.68 },
              { latitude: -33.42, longitude: -70.62 },
            ],
            steps: [
              {
                kind: "DELIVERY",
                order_id: "ORD-1",
                sequence: 1,
                location: { latitude: -33.41, longitude: -70.61 },
              },
            ],
          },
        ],
      },
    } as unknown as PlanningRun;

    expect(featureBounds(routeFeatureCollection(run), stopFeatureCollection(run))).toEqual([
      [-70.68, -33.46],
      [-70.61, -33.41],
    ]);
  });
});
