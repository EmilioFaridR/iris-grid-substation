import pytest
from shapely.geometry import Point

from app.parsers.geometry import (
    geometry_from_element,
    in_bbox,
    representative_point,
    usable_polygon,
    validate_polygon,
)


def pts(*pairs):
    return [{"lat": lat, "lon": lon} for lat, lon in pairs]


SQUARE = pts((51.0, 7.0), (51.0, 7.001), (51.001, 7.001), (51.001, 7.0), (51.0, 7.0))


def test_closed_way_becomes_polygon():
    res = geometry_from_element({"type": "way", "id": 1, "geometry": SQUARE})
    assert res.ok and res.kind == "polygon"
    valid, area, issues = validate_polygon(res.geometry, 2_000_000)
    assert valid and issues == []
    assert 7_000 < area < 8_000  # ~111 m x ~70 m


def test_unclosed_way_is_kept_as_linestring_and_flagged():
    res = geometry_from_element({"type": "way", "id": 1, "geometry": SQUARE[:-1]})
    assert res.ok and res.kind == "linestring"
    assert "unclosed_way" in res.issues


def test_self_intersecting_polygon_is_detected_not_dropped():
    bowtie = pts((51.0, 7.0), (51.001, 7.001), (51.0, 7.001), (51.001, 7.0), (51.0, 7.0))
    res = geometry_from_element({"type": "way", "id": 1, "geometry": bowtie})
    assert res.ok and res.kind == "polygon"
    valid, area, issues = validate_polygon(res.geometry, 2_000_000)
    assert not valid
    assert any(i.startswith("invalid_polygon:Self-intersection") for i in issues)
    assert area > 0                       # measured on the repaired shape
    assert usable_polygon(res.geometry).is_valid


def test_way_with_missing_vertex_is_incomplete():
    broken = SQUARE[:2] + [None] + SQUARE[3:]
    res = geometry_from_element({"type": "way", "id": 1, "geometry": broken})
    assert res.kind == "linestring"
    assert {"partial_geometry", "incomplete_ring"} <= set(res.issues)


@pytest.mark.parametrize("lat, lon", [(95.0, 7.0), (51.0, 190.0), (-91, 0)])
def test_out_of_range_coordinates_are_rejected(lat, lon):
    res = geometry_from_element({"type": "node", "id": 1, "lat": lat, "lon": lon})
    assert res.reject_reason == "coordinates_out_of_range"


def test_node_without_coordinates_is_rejected():
    assert geometry_from_element({"type": "node", "id": 1}).reject_reason == "missing_geometry"


def test_way_with_only_center_is_center_only():
    res = geometry_from_element({"type": "way", "id": 1, "center": {"lat": 51.45, "lon": 7.41}})
    assert res.ok and res.kind == "center_only"
    assert "no_full_geometry" in res.issues


def test_relation_multipolygon_is_assembled_from_open_ways_and_holes():
    sw, se, ne, nw = (51.0, 7.0), (51.0, 7.002), (51.002, 7.002), (51.002, 7.0)
    hole = pts((51.0009, 7.0009), (51.0009, 7.0011), (51.0011, 7.0011), (51.0011, 7.0009), (51.0009, 7.0009))
    rel = {
        "type": "relation", "id": 1,
        "members": [
            {"type": "way", "role": "outer", "geometry": pts(sw, se, ne)},
            {"type": "way", "role": "outer", "geometry": pts(ne, nw, sw)},
            {"type": "way", "role": "inner", "geometry": hole},
        ],
    }
    res = geometry_from_element(rel)
    assert res.ok and res.kind == "polygon"
    assert len(res.geometry.interiors) == 1
    assert not res.geometry.contains(Point(7.001, 51.001))   # inside the hole


def test_relation_that_cannot_be_assembled_falls_back_to_center():
    rel = {"type": "relation", "id": 1, "center": {"lat": 51.0, "lon": 7.0},
           "members": [{"type": "way", "role": "outer", "geometry": pts((51.0, 7.0), (51.0, 7.001))}]}
    res = geometry_from_element(rel)
    assert res.kind == "center_only"
    assert "relation_assembly_failed" in res.issues


def test_implausibly_large_area_is_flagged():
    huge = pts((50.0, 6.0), (50.0, 7.0), (51.0, 7.0), (51.0, 6.0), (50.0, 6.0))
    res = geometry_from_element({"type": "way", "id": 1, "geometry": huge})
    _valid, _area, issues = validate_polygon(res.geometry, 2_000_000)
    assert "area_implausibly_large" in issues


def test_representative_point_is_inside_concave_shape():
    u_shape = pts((51.0, 7.0), (51.0, 7.003), (51.003, 7.003), (51.003, 7.002),
                  (51.001, 7.002), (51.001, 7.001), (51.003, 7.001), (51.003, 7.0), (51.0, 7.0))
    res = geometry_from_element({"type": "way", "id": 1, "geometry": u_shape})
    assert res.geometry.covers(representative_point(res.geometry))


def test_in_bbox():
    assert in_bbox(Point(7.0, 51.0), (50.0, 6.0, 52.0, 8.0))
    assert not in_bbox(Point(9.9, 51.0), (50.0, 6.0, 52.0, 8.0))
