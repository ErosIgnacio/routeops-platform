from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import httpx

from routeops.application.ports.fixed_route import FixedRoadRoute
from routeops.domain.models import Coordinate
from routeops.infrastructure.routing.errors import RoutingCoverageError, RoutingDependencyError


@dataclass(frozen=True, slots=True)
class AllocationMatrix:
    durations: tuple[tuple[int, ...], ...]
    snaps: list[dict[str, object]]


class OsrmClient:
    version = "26.9.0"

    def __init__(
        self,
        base_url: str,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 20.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=read_timeout_seconds,
            pool=connect_timeout_seconds,
        )

    def duration_seconds(self, origin: Coordinate, destination: Coordinate) -> int:
        coordinates = (
            f"{origin.longitude},{origin.latitude};{destination.longitude},{destination.latitude}"
        )
        url = f"{self._base_url}/table/v1/driving/{coordinates}"
        try:
            response = httpx.get(
                url,
                params={"sources": "0", "destinations": "1", "annotations": "duration"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            duration = payload["durations"][0][0]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise RoutingDependencyError(f"OSRM table request failed: {exc}") from exc
        if duration is None:
            raise RoutingDependencyError("OSRM returned no route between allocation points")
        return round(float(duration))

    def duration_matrix(
        self, origins: tuple[Coordinate, ...], destinations: tuple[Coordinate, ...]
    ) -> tuple[tuple[int, ...], ...]:
        if not origins or not destinations:
            return tuple()
        coordinates = origins + destinations
        path = ";".join(f"{point.longitude},{point.latitude}" for point in coordinates)
        sources = ";".join(str(index) for index in range(len(origins)))
        targets = ";".join(str(index) for index in range(len(origins), len(coordinates)))
        try:
            response = httpx.get(
                f"{self._base_url}/table/v1/driving/{path}",
                params={
                    "sources": sources,
                    "destinations": targets,
                    "annotations": "duration",
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            raw = response.json()
            if raw.get("code") != "Ok":
                raise ValueError("OSRM table did not return Ok")
            rows = raw["durations"]
            if len(rows) != len(origins) or any(len(row) != len(destinations) for row in rows):
                raise ValueError("OSRM table dimensions do not match the request")
            if any(value is None for row in rows for value in row):
                raise ValueError("OSRM table contains an unreachable pair")
            return tuple(tuple(round(float(value)) for value in row) for row in rows)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RoutingDependencyError("OSRM allocation matrix failed") from exc

    def checked_duration_matrix(
        self,
        origins: tuple[Coordinate, ...],
        destinations: tuple[Coordinate, ...],
        max_snap_distance_m: float,
    ) -> AllocationMatrix:
        """Use the same OSRM table response for allocation times and directional snap evidence."""
        if not origins or not destinations:
            return AllocationMatrix(tuple(), [])
        coordinates = origins + destinations
        path = ";".join(f"{point.longitude},{point.latitude}" for point in coordinates)
        try:
            response = httpx.get(
                f"{self._base_url}/table/v1/driving/{path}",
                params={
                    "sources": ";".join(str(index) for index in range(len(origins))),
                    "destinations": ";".join(
                        str(index) for index in range(len(origins), len(coordinates))
                    ),
                    "annotations": "duration",
                },
                timeout=self._timeout,
            )
            payload = response.json()
            if payload.get("code") in ("NoSegment", "NoRoute"):
                originals: list[dict[str, object]] = [
                    {
                        "role": role,
                        "index": index,
                        "original": [point.longitude, point.latitude],
                        "snapped": None,
                        "distance_m": None,
                    }
                    for role, points in (("center", origins), ("order", destinations))
                    for index, point in enumerate(points)
                ]
                code = (
                    "ROUTING_POINT_UNCOVERED"
                    if payload["code"] == "NoSegment"
                    else "ROUTING_NO_PATH"
                )
                raise RoutingCoverageError(code, originals)
            response.raise_for_status()
            if payload.get("code") != "Ok":
                raise ValueError("OSRM table did not return Ok")
            rows = payload["durations"]
            if len(rows) != len(origins) or any(len(row) != len(destinations) for row in rows):
                raise ValueError("OSRM table dimensions do not match the request")
            snaps: list[dict[str, object]] = []
            for role, points, waypoints in (
                ("center", origins, payload["sources"]),
                ("order", destinations, payload["destinations"]),
            ):
                if len(points) != len(waypoints):
                    raise ValueError("OSRM waypoint dimensions do not match the request")
                for index, (point, waypoint) in enumerate(zip(points, waypoints, strict=True)):
                    location = waypoint["location"]
                    distance = float(waypoint["distance"])
                    if (
                        len(location) != 2
                        or not all(math.isfinite(float(value)) for value in location)
                        or not math.isfinite(distance)
                        or distance < 0
                    ):
                        raise ValueError("OSRM returned an invalid waypoint")
                    snaps.append(
                        {
                            "role": role,
                            "index": index,
                            "original": [point.longitude, point.latitude],
                            "snapped": [float(location[0]), float(location[1])],
                            "distance_m": round(distance, 3),
                        }
                    )
            if any(cast(float, item["distance_m"]) > max_snap_distance_m for item in snaps):
                raise RoutingCoverageError("ROUTING_SNAP_TOO_FAR", snaps)
            if any(value is None for row in rows for value in row):
                raise RoutingCoverageError("ROUTING_NO_PATH", snaps)
            durations = tuple(tuple(round(float(value)) for value in row) for row in rows)
            return AllocationMatrix(durations, snaps)
        except RoutingCoverageError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RoutingDependencyError("OSRM allocation matrix failed") from exc

    def route_sequence(
        self,
        points: tuple[Coordinate, ...],
        max_snap_distance_m: float,
    ) -> FixedRoadRoute:
        """Route supplied waypoints in their exact order; never use OSRM trip."""
        if len(points) < 2:
            raise ValueError("fixed road route requires endpoints")
        path = ";".join(f"{p.longitude},{p.latitude}" for p in points)
        try:
            response = httpx.get(
                f"{self._base_url}/route/v1/driving/{path}",
                params={"overview": "full", "geometries": "geojson", "steps": "false"},
                timeout=self._timeout,
            )
            payload = response.json()
            if payload.get("code") in ("NoSegment", "NoRoute"):
                raise RoutingCoverageError("ROUTING_NO_PATH", [])
            response.raise_for_status()
            if payload.get("code") != "Ok" or len(payload["waypoints"]) != len(points):
                raise ValueError("invalid fixed road response")
            route = payload["routes"][0]
            legs = route["legs"]
            if len(legs) != len(points) - 1:
                raise ValueError("fixed road leg count mismatch")
            snaps: list[dict[str, object]] = []
            for index, (point, waypoint) in enumerate(
                zip(points, payload["waypoints"], strict=True)
            ):
                distance = float(waypoint["distance"])
                location = waypoint["location"]
                if not math.isfinite(distance) or distance < 0 or len(location) != 2:
                    raise ValueError("invalid road waypoint")
                if not all(math.isfinite(float(v)) for v in location):
                    raise ValueError("invalid road coordinates")
                snaps.append(
                    {
                        "index": index,
                        "original": [point.longitude, point.latitude],
                        "snapped": location,
                        "distance_m": distance,
                    }
                )
            if any(cast(float, item["distance_m"]) > max_snap_distance_m for item in snaps):
                raise RoutingCoverageError("ROUTING_SNAP_TOO_FAR", snaps)
            if any(
                not math.isfinite(float(leg[key])) or float(leg[key]) < 0
                for leg in legs
                for key in ("duration", "distance")
            ):
                raise ValueError("invalid road leg metrics")
            geometry = tuple(
                Coordinate(float(lat), float(lon)) for lon, lat in route["geometry"]["coordinates"]
            )
            if len(geometry) < 2:
                raise ValueError("fixed route geometry unavailable")
            return FixedRoadRoute(
                tuple(round(float(leg["duration"])) for leg in legs),
                tuple(round(float(leg["distance"])) for leg in legs),
                geometry,
                tuple(snaps),
            )
        except RoutingCoverageError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
            raise RoutingDependencyError("OSRM fixed sequence evaluation failed") from exc

    def health(self) -> dict[str, str]:
        coordinates = "-70.6635,-33.4445;-70.6580,-33.4420"
        try:
            response = httpx.get(
                f"{self._base_url}/route/v1/driving/{coordinates}",
                params={"overview": "false"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            if response.json().get("code") != "Ok":
                raise ValueError("OSRM health route did not return Ok")
        except (httpx.HTTPError, ValueError) as exc:
            raise RoutingDependencyError(f"OSRM health check failed: {exc}") from exc
        return {"status": "ready", "version": self.version}
