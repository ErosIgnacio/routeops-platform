import { describe, expect, it } from "vitest";
import { validateStyleMin } from "@maplibre/maplibre-gl-style-spec";

import {
  createRouteMapStyle,
  overlayLayers,
  routeCasingLayer,
  routeLineLayer,
} from "./map-style";

describe("MapLibre route layers", () => {
  it("draws a high-contrast rounded route above its casing and below stops", () => {
    expect(overlayLayers.map((layer) => layer.id)).toEqual([
      "route-casing",
      "routes",
      "stop-casing",
      "stops",
    ]);
    expect(routeCasingLayer.layout).toMatchObject({
      "line-cap": "round",
      "line-join": "round",
    });
    expect(routeCasingLayer.paint).toMatchObject({
      "line-color": "#ffffff",
      "line-width": [
        "case",
        ["get", "selectionActive"],
        ["case", ["get", "selected"], 15, 11],
        13,
      ],
    });
    expect(routeLineLayer.layout).toMatchObject({
      "line-cap": "round",
      "line-join": "round",
    });
    expect(routeLineLayer.paint).toMatchObject({
      "line-color": ["get", "color"],
      "line-width": [
        "case",
        ["get", "selectionActive"],
        ["case", ["get", "selected"], 10, 5],
        7,
      ],
      "line-opacity": [
        "case",
        ["get", "selectionActive"],
        ["case", ["get", "selected"], 1, 0.32],
        1,
      ],
    });
  });

  it("declares RouteOps sources and overlays in the initial style", () => {
    const style = createRouteMapStyle();

    expect(Object.keys(style.sources)).toEqual(["osm", "routes", "stops"]);
    expect(style.layers.map((layer) => layer.id)).toEqual([
      "osm",
      "route-casing",
      "routes",
      "stop-casing",
      "stops",
    ]);
  });
  it("passes MapLibre's style specification validator", () => {
    expect(validateStyleMin(createRouteMapStyle())).toEqual([]);
  });
});
