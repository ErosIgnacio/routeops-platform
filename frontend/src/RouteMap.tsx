import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { fitMapResult, updateMapResult } from "./map-controller";
import { routeColorForVehicle } from "./map-data";
import { createRouteMapStyle } from "./map-style";
import type { PlanningRun } from "./types";

const routeLayerIds = ["route-casing", "routes", "stop-casing", "stops"] as const;
const readinessEvents = ["style.load", "load", "styledata", "sourcedata", "idle"] as const;

type RouteMapProps = {
  run: PlanningRun | null;
  selectedVehicleId: string | null;
  onShowAll?: () => void;
};

export function mapViewKey(run: PlanningRun | null, selectedVehicleId: string | null): string {
  return JSON.stringify([
    run?.run_id ?? null,
    selectedVehicleId,
    run?.result?.routes.map((route) => [
      route.source_vehicle_id,
      route.geometry,
      route.steps.map((step) => [step.kind, step.location, step.order_id]),
    ]) ?? [],
  ]);
}

type DebugState = {
  styleLoaded: boolean;
  routeSource: boolean;
  stopSource: boolean;
  layers: Record<string, { exists: boolean; visibility: string }>;
  routesReceived: Array<{
    vehicle: string;
    color: string;
    coordinates: number;
    finite: boolean;
    coordinateOrder: "[longitude, latitude]";
  }>;
  stopsReceived: number;
  stopsFinite: boolean;
  sourceFeatures: { routes: number | null; stops: number | null };
  renderedFeatures: Record<string, number | null>;
  events: Record<string, number>;
  sync: { attempts: number; applied: number };
  selectedVehicle: string | null;
  lastError: string | null;
};

function sourceFeatureCount(map: maplibregl.Map, sourceId: string): number | null {
  try {
    return map.getSource(sourceId) ? map.querySourceFeatures(sourceId).length : null;
  } catch {
    return null;
  }
}

function renderedFeatureCount(map: maplibregl.Map, layerId: string): number | null {
  try {
    return map.getLayer(layerId)
      ? map.queryRenderedFeatures({ layers: [layerId] }).length
      : null;
  } catch {
    return null;
  }
}

function readDebugState(
  map: maplibregl.Map,
  run: PlanningRun | null,
  selectedVehicleId: string | null,
  events: Record<string, number>,
  sync: { attempts: number; applied: number },
  lastError: string | null,
): DebugState {
  const layerIds = [...routeLayerIds];
  const layers = Object.fromEntries(
    layerIds.map((layerId) => {
      const exists = Boolean(map.getLayer(layerId));
      return [
        layerId,
        {
          exists,
          visibility: exists
            ? String(map.getLayoutProperty(layerId, "visibility") ?? "visible")
            : "missing",
        },
      ];
    }),
  );
  const renderedFeatures = Object.fromEntries(
    layerIds.map((layerId) => [layerId, renderedFeatureCount(map, layerId)]),
  );
  const routes = run?.result?.routes ?? [];
  const stops = routes.flatMap((route) => route.steps);

  return {
    styleLoaded: map.isStyleLoaded() === true,
    routeSource: Boolean(map.getSource("routes")),
    stopSource: Boolean(map.getSource("stops")),
    layers,
    routesReceived: routes.map((route) => ({
      vehicle: route.source_vehicle_id,
      color: routeColorForVehicle(route.source_vehicle_id),
      coordinates: route.geometry.length,
      finite: route.geometry.every(
        (point) => Number.isFinite(point.longitude) && Number.isFinite(point.latitude),
      ),
      coordinateOrder: "[longitude, latitude]",
    })),
    stopsReceived: stops.length,
    stopsFinite: stops.every(
      (stop) => Number.isFinite(stop.location.longitude) && Number.isFinite(stop.location.latitude),
    ),
    sourceFeatures: {
      routes: sourceFeatureCount(map, "routes"),
      stops: sourceFeatureCount(map, "stops"),
    },
    renderedFeatures,
    events: { ...events },
    sync: { ...sync },
    selectedVehicle: selectedVehicleId,
    lastError,
  };
}

export function RouteMap({ run, selectedVehicleId, onShowAll }: RouteMapProps) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const lastViewKeyRef = useRef<string | null>(null);
  const runRef = useRef(run);
  const selectedVehicleRef = useRef(selectedVehicleId);
  const lastErrorRef = useRef<string | null>(null);
  const eventCountsRef = useRef<Record<string, number>>({});
  const syncRef = useRef({ attempts: 0, applied: 0 });
  const query =
    typeof window === "undefined"
      ? new URLSearchParams()
      : new URLSearchParams(window.location.search);
  const debugEnabled = query.get("debugMap") === "1";
  const [debugState, setDebugState] = useState<DebugState | null>(null);
  runRef.current = run;
  selectedVehicleRef.current = selectedVehicleId;

  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: createRouteMapStyle(),
      center: [-70.651, -33.441],
      zoom: 13.1,
      attributionControl: {},
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => map.resize());
    observer?.observe(container.current);

    const onMapClick = (event: maplibregl.MapMouseEvent) => {
      const features = map.queryRenderedFeatures(event.point, { layers: ["stops"] });
      if (!features.length) return;
      const content = document.createElement("div");
      content.className = "stop-popup";
      const title = document.createElement("strong");
      title.textContent = "Paradas en este punto";
      content.append(title);
      const seen = new Set<string>();
      for (const feature of features) {
        const props = feature.properties;
        const label = `${props.kind === "DELIVERY" ? "Entrega" : "CD"} · ${props.order} · ${props.vehicle} · #${props.sequence}`;
        if (seen.has(label)) continue;
        seen.add(label);
        const line = document.createElement("div");
        line.textContent = label;
        content.append(line);
      }
      new maplibregl.Popup({ closeButton: true })
        .setLngLat(event.lngLat)
        .setDOMContent(content)
        .addTo(map);
    };
    map.on("click", onMapClick);

    const refreshDebugState = () => {
      if (!debugEnabled) return;
      setDebugState(
        readDebugState(
          map,
          runRef.current,
          selectedVehicleRef.current,
          eventCountsRef.current,
          syncRef.current,
          lastErrorRef.current,
        ),
      );
    };
    const record = (eventName: string, refresh = false) => () => {
      eventCountsRef.current[eventName] = (eventCountsRef.current[eventName] ?? 0) + 1;
      if (debugEnabled) console.info(`[RouteMap] ${eventName}`);
      if (refresh) refreshDebugState();
    };
    const onLoad = record("load", true);
    const onStyleLoad = record("style.load", true);
    const onSourceData = record("sourcedata");
    const onIdle = record("idle", true);
    const onError = (event: maplibregl.ErrorEvent) => {
      lastErrorRef.current = event.error?.message ?? "Unknown MapLibre error";
      eventCountsRef.current.error = (eventCountsRef.current.error ?? 0) + 1;
      console.error("[RouteMap] error", event.error ?? event);
      refreshDebugState();
    };

    map.on("load", onLoad);
    map.on("style.load", onStyleLoad);
    map.on("sourcedata", onSourceData);
    map.on("idle", onIdle);
    map.on("error", onError);

    return () => {
      observer?.disconnect();
      map.off("click", onMapClick);
      map.off("load", onLoad);
      map.off("style.load", onStyleLoad);
      map.off("sourcedata", onSourceData);
      map.off("idle", onIdle);
      map.off("error", onError);
      mapRef.current = null;
      map.remove();
    };
  }, [debugEnabled]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    let applied = false;
    const viewKey = mapViewKey(run, selectedVehicleId);
    const tryApply = () => {
      if (applied) return;
      syncRef.current.attempts += 1;
      try {
        applied = updateMapResult(
          map, runRef.current, selectedVehicleRef.current,
          lastViewKeyRef.current !== viewKey,
        );
        if (applied) {
          lastViewKeyRef.current = viewKey;
          syncRef.current.applied += 1;
          readinessEvents.forEach((eventName) => map.off(eventName, tryApply));
        }
      } catch (error) {
        lastErrorRef.current = error instanceof Error ? error.message : String(error);
        console.error("[RouteMap] synchronization error", error);
      }
    };

    readinessEvents.forEach((eventName) => map.on(eventName, tryApply));
    tryApply();
    return () => {
      readinessEvents.forEach((eventName) => map.off(eventName, tryApply));
    };
  }, [run, selectedVehicleId]);

  return (
    <>
      <div className="map-actions">
        <button type="button" onClick={() => fitMapResult(mapRef.current!, runRef.current, selectedVehicleRef.current)} disabled={!run?.result?.routes.length}>Centrar rutas</button>
        {onShowAll && <button type="button" onClick={onShowAll} disabled={!selectedVehicleId}>Mostrar todas</button>}
      </div>
      <div className="route-map" ref={container} aria-label="Optimized route map" />
      {debugEnabled && (
        <aside className="map-debug" aria-label="MapLibre runtime diagnostics">
          <strong>MapLibre runtime diagnostics</strong>
          <pre>{JSON.stringify(debugState ?? { waitingForMapEvent: true }, null, 2)}</pre>
        </aside>
      )}
    </>
  );
}
