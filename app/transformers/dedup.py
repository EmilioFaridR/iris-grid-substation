from __future__ import annotations

from shapely.strtree import STRtree

from app.models import SubstationRecord
from app.parsers.geometry import usable_polygon

IOU_THRESHOLD = 0.9


def _sort_key(rec: SubstationRecord):
    return {"node": 0, "way": 1, "relation": 2}[rec.osm_type], rec.osm_id


def mark_duplicates(substations: list[SubstationRecord]) -> int:
    areas = sorted((s for s in substations if s.has_polygon), key=_sort_key)
    if not areas:
        return 0
    polygons = [usable_polygon(s.geometry) for s in areas]
    tree = STRtree(polygons)
    marked = 0

    # 1) node inside area
    for node in sorted((s for s in substations if s.geom_kind == "point"), key=_sort_key):
        hits = sorted(tree.query(node.geometry, predicate="intersects"),
                      key=lambda i: (polygons[i].area, _sort_key(areas[i])))
        for idx in hits:
            area = areas[idx]
            if set(node.hv_levels_kv) & set(area.hv_levels_kv):
                node.is_duplicate, node.duplicate_of = True, area.key
                node.flag("node_inside_substation_area")
                marked += 1
                break
            node.flag("node_inside_other_voltage_substation")

    # 2) area drawn twice
    for i, rec in enumerate(areas):
        if rec.is_duplicate:
            continue
        for j in sorted(tree.query(polygons[i], predicate="intersects")):
            if j <= i or areas[j].is_duplicate:
                continue
            other = areas[j]
            if set(rec.hv_levels_kv) != set(other.hv_levels_kv):
                continue
            inter = polygons[i].intersection(polygons[j]).area
            union = polygons[i].union(polygons[j]).area
            if union > 0 and inter / union >= IOU_THRESHOLD:
                other.is_duplicate, other.duplicate_of = True, rec.key
                other.flag("overlapping_duplicate_area")
                marked += 1
    return marked
