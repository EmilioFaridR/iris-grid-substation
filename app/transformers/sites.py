from __future__ import annotations

import re
from collections import Counter

from shapely.strtree import STRtree

from app.models import SubstationRecord
from app.parsers.geometry import to_metric

_GENERIC_WORDS = re.compile(
    r"\b(station|umspannwerk|umspannanlage|umspannstation|schaltanlage|uw|ua|"
    r"unterwerk|substation)\b",
    re.IGNORECASE,
)


def normalise_name(value: str | None) -> str | None:
    if not value:
        return None
    text = _GENERIC_WORDS.sub(" ", value.casefold())
    text = re.sub(r"[^\w]+", " ", text)
    text = " ".join(text.split())
    return text or None


def _names(rec: SubstationRecord) -> set[str]:
    out = set()
    for key in ("name", "alt_name", "official_name", "short_name"):
        for part in (rec.tags.get(key) or "").split(";"):
            norm = normalise_name(part)
            if norm:
                out.add(norm)
    return out


def _is_traction(rec: SubstationRecord) -> bool:
    return rec.substation_type == "traction" or (
        rec.frequency_hz is not None and rec.frequency_hz < 20
    )


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def assign_sites(
    substations: list[SubstationRecord],
    name_max_distance_m: float = 500.0,
    adjacency_max_distance_m: float = 30.0,
) -> Counter:
    recs = sorted((s for s in substations if not s.is_duplicate), key=lambda s: s.key)
    stats: Counter = Counter()
    if not recs:
        return stats

    metric_geoms = [to_metric(r.geometry) for r in recs]
    metric_points = [to_metric(r.centroid) for r in recs]
    names = [_names(r) for r in recs]
    traction = [_is_traction(r) for r in recs]
    tree = STRtree(metric_geoms)
    search = max(name_max_distance_m, adjacency_max_distance_m)

    uf = _UnionFind(len(recs))
    edge_reason: dict[tuple[int, int], str] = {}
    for i in range(len(recs)):
        for j in sorted(tree.query(metric_geoms[i], predicate="dwithin", distance=search)):
            if j <= i or traction[i] != traction[j]:
                continue
            a, b = recs[i], recs[j]
            reason = None
            if a.wikidata and a.wikidata == b.wikidata:
                reason = "same_wikidata"
            elif names[i] & names[j] and metric_points[i].distance(metric_points[j]) <= name_max_distance_m:
                reason = f"same_name_within_{int(name_max_distance_m)}m"
            elif metric_geoms[i].distance(metric_geoms[j]) <= adjacency_max_distance_m:
                reason = f"adjacent_within_{int(adjacency_max_distance_m)}m"
            if reason:
                uf.union(i, j)
                edge_reason[(i, j)] = reason

    groups: dict[int, list[int]] = {}
    for i in range(len(recs)):
        groups.setdefault(uf.find(i), []).append(i)

    for members in groups.values():
        member_set = set(members)
        site_id = min(recs[m].key for m in members)
        reasons = sorted({r for (i, j), r in edge_reason.items() if i in member_set})
        for m in members:
            recs[m].site_id = site_id
            recs[m].site_group_reason = ",".join(reasons) if len(members) > 1 else None
        stats["sites"] += 1
        if len(members) > 1:
            stats["multi_element_sites"] += 1
            stats["elements_in_multi_element_sites"] += len(members)

    # duplicates point at the site of the element they duplicate
    by_key = {r.key: r for r in recs}
    for dup in (s for s in substations if s.is_duplicate):
        target = by_key.get(dup.duplicate_of or "")
        dup.site_id = target.site_id if target else dup.key
        dup.site_group_reason = "duplicate"
    return stats
