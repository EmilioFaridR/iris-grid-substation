
from __future__ import annotations

from collections import Counter

from shapely.strtree import STRtree

from app.models import SubstationRecord, TransformerRecord
from app.parsers.geometry import usable_polygon

CONTAINED = "contained"
CONTAINED_AMBIGUOUS = "contained_ambiguous"
UNASSIGNED = "unassigned"


def assign_transformers(
    substations: list[SubstationRecord], transformers: list[TransformerRecord]
) -> Counter:
    stats: Counter = Counter()
    hosts = sorted(
        (s for s in substations if s.has_polygon and not s.is_duplicate),
        key=lambda s: (s.osm_type, s.osm_id),
    )
    polygons = [usable_polygon(s.geometry) for s in hosts]
    tree = STRtree(polygons) if polygons else None

    for trafo in sorted(transformers, key=lambda t: (t.osm_type, t.osm_id)):
        hits = [] if tree is None else list(tree.query(trafo.point, predicate="intersects"))
        if not hits:
            trafo.substation_key, trafo.assignment_status = None, UNASSIGNED
            stats[UNASSIGNED] += 1
            continue
        hits.sort(key=lambda i: (polygons[i].area, hosts[i].osm_type, hosts[i].osm_id))
        trafo.substation_key = hosts[hits[0]].key
        if len(hits) == 1:
            trafo.assignment_status = CONTAINED
        else:
            trafo.assignment_status = CONTAINED_AMBIGUOUS
            trafo.flag("inside_multiple_substations")
        stats[trafo.assignment_status] += 1
    return stats
