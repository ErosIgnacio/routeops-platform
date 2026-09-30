from __future__ import annotations

from routeops.domain.models import Coordinate


def decode_polyline(encoded: str, precision: int = 5) -> tuple[Coordinate, ...]:
    """Decode a Google encoded polyline into domain coordinates."""
    coordinates: list[Coordinate] = []
    latitude = 0
    longitude = 0
    index = 0
    factor = 10**precision

    while index < len(encoded):
        latitude_delta, index = _decode_value(encoded, index)
        longitude_delta, index = _decode_value(encoded, index)
        latitude += latitude_delta
        longitude += longitude_delta
        coordinates.append(Coordinate(latitude=latitude / factor, longitude=longitude / factor))
    return tuple(coordinates)


def _decode_value(encoded: str, index: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if index >= len(encoded):
            raise ValueError("truncated encoded polyline")
        value = ord(encoded[index]) - 63
        index += 1
        result |= (value & 0x1F) << shift
        shift += 5
        if value < 0x20:
            break
    delta = ~(result >> 1) if result & 1 else result >> 1
    return delta, index
