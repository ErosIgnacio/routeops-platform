import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PlanningRun } from "./types";

const maplibreState = vi.hoisted(() => ({
  instances: [] as any[],
  sourcesReadyByDefault: false,
}));

vi.mock("maplibre-gl", () => {
  class MockMap {
    options: any;
    sourcesReady = maplibreState.sourcesReadyByDefault;
    handlers = new globalThis.Map<string, Set<() => void>>();
    routeSetData = vi.fn();
    stopSetData = vi.fn();
    fitBounds = vi.fn();
    addControl = vi.fn();
    remove = vi.fn();

    constructor(options: any) {
      this.options = options;
      maplibreState.instances.push(this);
    }

    isStyleLoaded() {
      return this.sourcesReady;
    }

    getSource(id: string) {
      if (!this.sourcesReady) return undefined;
      if (id === "routes") return { setData: this.routeSetData };
      if (id === "stops") return { setData: this.stopSetData };
      return undefined;
    }

    on(event: string, handler: () => void) {
      const handlers = this.handlers.get(event) ?? new Set<() => void>();
      handlers.add(handler);
      this.handlers.set(event, handlers);
      return this;
    }

    off(event: string, handler: () => void) {
      this.handlers.get(event)?.delete(handler);
      return this;
    }

    emit(event: string) {
      [...(this.handlers.get(event) ?? [])].forEach((handler) => handler());
    }
  }

  return {
    Map: MockMap,
    NavigationControl: class MockNavigationControl {},
  };
});

import { RouteMap } from "./RouteMap";

function runFor(vehicle: string, longitude: number): PlanningRun {
  return {
    result: {
      routes: [
        {
          vehicle_id: `${vehicle}-id`,
          source_vehicle_id: vehicle,
          distribution_center_id: "DC-CENTRO",
          geometry: [
            { longitude, latitude: -33.44 },
            { longitude: Number((longitude + 0.01).toFixed(5)), latitude: -33.43 },
          ],
          steps: [],
        },
      ],
    },
  } as unknown as PlanningRun;
}

describe("RouteMap", () => {
  beforeEach(() => {
    maplibreState.instances.length = 0;
    maplibreState.sourcesReadyByDefault = false;
  });

  it("keeps retrying until both sources exist, even if style.load timing was missed", () => {
    const run = runFor("VEH-CENTRO-01", -70.66);
    render(<RouteMap run={run} selectedVehicleId={null} />);
    const map = maplibreState.instances.at(-1)!;

    expect(Object.keys(map.options.style.sources)).toEqual(["osm", "routes", "stops"]);
    expect(map.routeSetData).not.toHaveBeenCalled();

    act(() => {
      map.emit("style.load");
      map.sourcesReady = true;
      map.emit("sourcedata");
    });

    expect(map.routeSetData).toHaveBeenCalledOnce();
    expect(map.stopSetData).toHaveBeenCalledOnce();
    expect(map.fitBounds).toHaveBeenCalledWith(
      [
        [-70.66, -33.44],
        [-70.65, -33.43],
      ],
      expect.objectContaining({ maxZoom: 15.25 }),
    );
  });

  it("updates immediately when the style is ready and again after reoptimization", () => {
    maplibreState.sourcesReadyByDefault = true;
    const firstRun = runFor("VEH-CENTRO-01", -70.66);
    const { rerender } = render(
      <RouteMap run={firstRun} selectedVehicleId={null} />,
    );
    const map = maplibreState.instances.at(-1)!;

    expect(map.routeSetData).toHaveBeenCalledOnce();

    rerender(
      <RouteMap
        run={runFor("VEH-ORIENTE-01", -70.64)}
        selectedVehicleId="VEH-ORIENTE-01"
      />,
    );

    expect(map.routeSetData).toHaveBeenCalledTimes(2);
    expect(map.routeSetData.mock.calls[1]?.[0].features[0].properties).toMatchObject({
      vehicle: "VEH-ORIENTE-01",
      selected: true,
      selectionActive: true,
    });
  });
});
