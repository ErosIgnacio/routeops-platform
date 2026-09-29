from routeops.infrastructure.routing.polyline import decode_polyline


def test_decodes_standard_polyline() -> None:
    points = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")

    assert [(point.latitude, point.longitude) for point in points] == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]
