from __future__ import annotations

import httpx
import pytest

from routeops.domain.models import Coordinate
from routeops.infrastructure.routing.errors import RoutingCoverageError, RoutingDependencyError
from routeops.infrastructure.routing.osrm import OsrmClient


def _response(
    source_distance: float, destination_distance: float, duration: float | None = 42.5
) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("GET", "http://osrm/table"),
        json={
            "code": "Ok",
            "sources": [{"location": [-70.6635, -33.4445], "distance": source_distance}],
            "destinations": [{"location": [-70.66, -33.446], "distance": destination_distance}],
            "durations": [[duration]],
        },
    )


def test_checked_matrix_retains_directional_snap_and_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _response(20, 30))
    result = OsrmClient("http://osrm").checked_duration_matrix(
        (Coordinate(-33.4445, -70.6635),), (Coordinate(-33.446, -70.66),), 250
    )
    assert result.durations == ((42,),)
    assert [item["distance_m"] for item in result.snaps] == [20, 30]
    assert result.snaps[0]["original"] == [-70.6635, -33.4445]


def test_excessive_snap_and_unreachable_route_are_coverage_not_stock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = OsrmClient("http://osrm")
    origins = (Coordinate(-33.44, -70.7),)
    destinations = (Coordinate(-33.446, -70.66),)
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _response(1902, 30))
    with pytest.raises(RoutingCoverageError) as far:
        client.checked_duration_matrix(origins, destinations, 250)
    assert far.value.code == "ROUTING_SNAP_TOO_FAR"
    assert far.value.evidence[0]["distance_m"] == 1902

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _response(20, 30, None))
    with pytest.raises(RoutingCoverageError) as no_path:
        client.checked_duration_matrix(origins, destinations, 250)
    assert no_path.value.code == "ROUTING_NO_PATH"


def test_network_failure_is_not_reported_as_missing_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", fail)
    with pytest.raises(RoutingDependencyError):
        OsrmClient("http://osrm").checked_duration_matrix(
            (Coordinate(-33.4445, -70.6635),), (Coordinate(-33.446, -70.66),), 250
        )
