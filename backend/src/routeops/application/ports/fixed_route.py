"""Road evaluation of a supplied sequence, without order optimization."""

from dataclasses import dataclass
from typing import Protocol

from routeops.domain.models import Coordinate


@dataclass(frozen=True, slots=True)
class FixedRoadRoute:
    durations: tuple[int, ...]
    distances: tuple[int, ...]
    geometry: tuple[Coordinate, ...]
    snaps: tuple[dict[str, object], ...]


class FixedRouteGateway(Protocol):
    def route_sequence(
        self,
        points: tuple[Coordinate, ...],
        max_snap_distance_m: float,
    ) -> FixedRoadRoute: ...
