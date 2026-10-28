from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from pyproj import Geod, Transformer
from shapely import make_valid
from shapely import transform as shapely_transform
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union
from shapely.validation import explain_validity

METRIC_CRS = "EPSG:25832"  # ETRS89 / UTM zone 32N - standard CRS for NRW / Germany
_GEOD = Geod(ellps="WGS84")


@dataclass
class GeometryResult:
    geometry: BaseGeometry | None
    kind: str | None
    issues: list[str] = field(default_factory=list)
    reject_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.reject_reason is None


# Projection helpers


@lru_cache(maxsize=1)
def _to_metric():
    return Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True).transform


def to_metric(geom: BaseGeometry) -> BaseGeometry:
    return shapely_transform(geom, _to_metric(), interleaved=False)


def geodesic_area_m2(geom: BaseGeometry) -> float:
    area, _perimeter = _GEOD.geometry_area_perimeter(geom)
    return abs(area)


# Validation helpers


def _coords_in_range(lat, lon) -> bool:
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


def _points_from(geometry_list) -> tuple[list[tuple[float, float]], bool, bool]:
    """Return ((lon, lat) list, had_missing, had_out_of_range)."""
    coords: list[tuple[float, float]] = []
    missing = out_of_range = False
    for point in geometry_list or []:
        if not point or point.get("lat") is None or point.get("lon") is None:
            missing = True
            continue
        lat, lon = float(point["lat"]), float(point["lon"])
        if not _coords_in_range(lat, lon):
            out_of_range = True
            continue
        coords.append((lon, lat))
    return coords, missing, out_of_range


def usable_polygon(geom: BaseGeometry) -> BaseGeometry:
    return geom if geom.is_valid else make_valid(geom)


# Element -> geometry


def _center(element: dict) -> GeometryResult | None:
    center = element.get("center")
    if not center:
        return None
    lat, lon = center.get("lat"), center.get("lon")
    if not _coords_in_range(lat, lon):
        return GeometryResult(None, None, reject_reason="coordinates_out_of_range")
    return GeometryResult(Point(float(lon), float(lat)), "center_only", ["no_full_geometry"])


def _node(element: dict) -> GeometryResult:
    lat, lon = element.get("lat"), element.get("lon")
    if lat is None or lon is None:
        return GeometryResult(None, None, reject_reason="missing_geometry")
    if not _coords_in_range(lat, lon):
        return GeometryResult(None, None, reject_reason="coordinates_out_of_range")
    return GeometryResult(Point(float(lon), float(lat)), "point")


def _way(element: dict) -> GeometryResult:
    if "geometry" not in element:
        return _center(element) or GeometryResult(None, None, reject_reason="missing_geometry")

    coords, missing, out_of_range = _points_from(element["geometry"])
    issues = []
    if missing:
        issues.append("partial_geometry")
    if out_of_range:
        return GeometryResult(None, None, reject_reason="coordinates_out_of_range")

    closed = len(coords) >= 4 and coords[0] == coords[-1]
    if closed and not missing:
        return GeometryResult(Polygon(coords), "polygon", issues)
    if len(coords) >= 2:
        issues.append("unclosed_way" if not missing else "incomplete_ring")
        return GeometryResult(LineString(coords), "linestring", issues)
    center = _center(element)
    if center is not None and center.ok:
        center.issues = issues + center.issues
        return center
    return GeometryResult(None, None, reject_reason="missing_geometry")


def _relation(element: dict) -> GeometryResult:
    outers: list[LineString] = []
    inners: list[LineString] = []
    issues: list[str] = []
    for member in element.get("members") or []:
        if member.get("type") != "way":
            continue
        coords, missing, out_of_range = _points_from(member.get("geometry"))
        if out_of_range:
            return GeometryResult(None, None, reject_reason="coordinates_out_of_range")
        if missing:
            issues.append("partial_member_geometry")
        if len(coords) < 2:
            continue
        role = member.get("role") or "outer"
        (inners if role == "inner" else outers).append(LineString(coords))

    if outers:
        outer_polys = list(polygonize(unary_union(outers)))
        if outer_polys:
            shape = unary_union(outer_polys)
            if inners:
                inner_polys = list(polygonize(unary_union(inners)))
                if inner_polys:
                    shape = shape.difference(unary_union(inner_polys))
            if isinstance(shape, Polygon):
                return GeometryResult(shape, "polygon", issues)
            if isinstance(shape, MultiPolygon):
                return GeometryResult(shape, "multipolygon", issues)
        issues.append("relation_assembly_failed")
    else:
        issues.append("relation_without_way_geometry")

    center = _center(element)
    if center is not None and center.ok:
        center.issues = issues + center.issues
        return center
    return GeometryResult(None, None, issues, reject_reason="missing_geometry")


def geometry_from_element(element: dict) -> GeometryResult:
    """Build a WGS84 geometry for a node / way / relation and list its issues."""
    kind = element.get("type")
    if kind == "node":
        return _node(element)
    if kind == "way":
        return _way(element)
    if kind == "relation":
        return _relation(element)
    return GeometryResult(None, None, reject_reason="unsupported_element_type")


def validate_polygon(geom: BaseGeometry, max_area_m2: float) -> tuple[bool, float | None, list[str]]:
    """Return (is_valid, area_m2, issues) for a polygonal geometry."""
    issues: list[str] = []
    valid = bool(geom.is_valid)
    if not valid:
        issues.append(f"invalid_polygon:{explain_validity(geom).split('[')[0].strip()}")
    # area of the repaired shape: a self-intersecting ring would otherwise cancel itself out
    area = geodesic_area_m2(usable_polygon(geom))
    if area <= 0:
        issues.append("zero_area")
        valid = False
    elif area > max_area_m2:
        issues.append("area_implausibly_large")
    return valid, area, issues


def representative_point(geom: BaseGeometry) -> Point:
    """Centroid if it lies inside the shape, otherwise a guaranteed interior point."""
    if geom.geom_type == "Point":
        return geom
    centroid = geom.centroid
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        candidate = usable_polygon(geom)
        if not candidate.covers(centroid):
            return candidate.representative_point()
    return centroid


def in_bbox(point: Point, bbox: tuple[float, float, float, float]) -> bool:
    south, west, north, east = bbox
    return south <= point.y <= north and west <= point.x <= east
