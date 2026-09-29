import type {
  CircleLayerSpecification,
  LineLayerSpecification,
  StyleSpecification,
} from "maplibre-gl";

import { routeFeatureCollection, stopFeatureCollection } from "./map-data";

const roundedLineLayout: LineLayerSpecification["layout"] = {
  "line-cap": "round",
  "line-join": "round",
};

export const routeCasingLayer: LineLayerSpecification = {
  id: "route-casing",
  type: "line",
  source: "routes",
  layout: roundedLineLayout,
  paint: {
    "line-color": "#ffffff",
    "line-width": [
      "case",
      ["get", "selectionActive"],
      ["case", ["get", "selected"], 15, 11],
      13,
    ],
    "line-opacity": [
      "case",
      ["get", "selectionActive"],
      ["case", ["get", "selected"], 1, 0.42],
      0.98,
    ],
  },
};

export const routeLineLayer: LineLayerSpecification = {
  id: "routes",
  type: "line",
  source: "routes",
  layout: roundedLineLayout,
  paint: {
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
  },
};

export const stopCasingLayer: CircleLayerSpecification = {
  id: "stop-casing",
  type: "circle",
  source: "stops",
  paint: {
    "circle-radius": ["case", ["==", ["get", "kind"], "DELIVERY"], 11, 14],
    "circle-color": "#ffffff",
    "circle-opacity": [
      "case",
      ["get", "selectionActive"],
      ["case", ["get", "selected"], 1, 0.42],
      1,
    ],
  },
};

export const stopMarkerLayer: CircleLayerSpecification = {
  id: "stops",
  type: "circle",
  source: "stops",
  paint: {
    "circle-radius": ["case", ["==", ["get", "kind"], "DELIVERY"], 7, 10],
    "circle-color": ["get", "color"],
    "circle-opacity": [
      "case",
      ["get", "selectionActive"],
      ["case", ["get", "selected"], 1, 0.32],
      1,
    ],
    "circle-stroke-color": "#ffffff",
    "circle-stroke-width": 2,
  },
};

export const overlayLayers = [
  routeCasingLayer,
  routeLineLayer,
  stopCasingLayer,
  stopMarkerLayer,
] as const;

export function createRouteMapStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
      },
      routes: {
        type: "geojson",
        data: routeFeatureCollection(null),
      },
      stops: {
        type: "geojson",
        data: stopFeatureCollection(null),
      },
    },
    layers: [{ id: "osm", type: "raster", source: "osm" }, ...overlayLayers],
  };
}
