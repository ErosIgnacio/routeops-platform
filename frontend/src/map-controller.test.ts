import { describe, expect, it, vi } from "vitest";

import { type MapResultTarget, updateMapResult } from "./map-controller";
import type { PlanningRun } from "./types";

function runFor(vehicle: string, longitude: number): PlanningRun {
  return {
    result: {
      routes: [
        {
          source_vehicle_id: vehicle,
          distribution_center_id: "DC-1",
          geometry: [
            { latitude: -33.44, longitude },
            { latitude: -33.43, longitude: longitude + 0.01 },
          ],
          steps: [],
        },
      ],
    },
  } as unknown as PlanningRun;
}

describe("updateMapResult", () => {
  it("updates both GeoJSON sources and refits when the run changes", () => {
    const routeSetData = vi.fn();
    const stopSetData = vi.fn();
    const fitBounds = vi.fn();
    const map = {
      getSource: vi.fn((id: string) =>
        id === "routes" ? { setData: routeSetData } : { setData: stopSetData },
      ),
      fitBounds,
    } as unknown as MapResultTarget;

    expect(updateMapResult(map, runFor("VEH-CENTRO-01", -70.66))).toBe(true);
    expect(updateMapResult(map, runFor("VEH-ORIENTE-01", -70.64))).toBe(true);

    expect(routeSetData).toHaveBeenCalledTimes(2);
    expect(stopSetData).toHaveBeenCalledTimes(2);
    expect(routeSetData.mock.calls[0]?.[0].features[0].properties.color).toBe("#3338d6");
    expect(routeSetData.mock.calls[1]?.[0].features[0].properties.color).toBe("#eb1010");
    expect(fitBounds).toHaveBeenNthCalledWith(
      2,
      [
        [-70.64, -33.44],
        [-70.63, -33.43],
      ],
      {
        padding: { top: 64, right: 64, bottom: 64, left: 64 },
        maxZoom: 15.25,
        duration: 450,
      },
    );
  });
});
