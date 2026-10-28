from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

from shapely.geometry import mapping

from app.runners.process_runner import ProcessResult

SUBSTATION_COLUMNS = [
    "osm_type", "osm_id", "site_id", "name", "operators", "substation_type", "lifecycle",
    "is_grid_candidate", "non_grid_reason", "is_duplicate", "duplicate_of",
    "voltage_raw", "hv_levels_kv", "max_voltage_kv",
    "mva_status", "mva_value", "mva_level_kv", "mva_is_lower_bound", "mva_unknown_reason",
    "transformer_count", "transformer_rated_count", "rating_raw", "rating_status",
    "geom_kind", "geom_valid", "area_m2", "lat", "lon", "quality_flags", "osm_url",
]
TRANSFORMER_COLUMNS = [
    "osm_type", "osm_id", "substation_key", "assignment_status", "name", "ref",
    "transformer_type", "primary_v", "secondary_v", "tertiary_v", "rating_raw",
    "rating_status", "rating_mva", "rating_reject_reason", "lat", "lon", "quality_flags",
]


def _fmt(value):
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ";".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.1f}"
    return value


def _substation_dict(s) -> dict:
    return {
        "osm_type": s.osm_type, "osm_id": s.osm_id, "site_id": s.site_id, "name": s.name,
        "operators": s.operators, "substation_type": s.substation_type, "lifecycle": s.lifecycle,
        "is_grid_candidate": s.is_grid_candidate, "non_grid_reason": s.non_grid_reason,
        "is_duplicate": s.is_duplicate, "duplicate_of": s.duplicate_of,
        "voltage_raw": s.voltage_raw, "hv_levels_kv": s.hv_levels_kv,
        "max_voltage_kv": s.max_voltage_kv, "mva_status": s.mva_status,
        "mva_value": s.mva_value, "mva_level_kv": s.mva_level_kv,
        "mva_is_lower_bound": s.mva_is_lower_bound, "mva_unknown_reason": s.mva_unknown_reason,
        "transformer_count": s.transformer_count, "transformer_rated_count": s.transformer_rated_count,
        "rating_raw": s.rating_raw, "rating_status": s.rating_status,
        "geom_kind": s.geom_kind, "geom_valid": s.geom_valid, "area_m2": s.area_m2,
        "lat": round(s.centroid.y, 7), "lon": round(s.centroid.x, 7),
        "quality_flags": sorted(s.quality_flags),
        "osm_url": f"https://www.openstreetmap.org/{s.osm_type}/{s.osm_id}",
    }


def export(result: ProcessResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "substations_csv": out_dir / "substations_mva.csv",
        "substations_geojson": out_dir / "substations_mva.geojson",
        "transformers_csv": out_dir / "substation_transformers.csv",
        "rejects_csv": out_dir / "ingest_rejects.csv",
    }

    with paths["substations_csv"].open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUBSTATION_COLUMNS)
        writer.writeheader()
        for s in result.substations:
            writer.writerow({k: _fmt(v) for k, v in _substation_dict(s).items()})

    features = []
    for s in result.substations:
        props = _substation_dict(s)
        props["mva_value"] = None if s.mva_value is None else float(s.mva_value)
        props["capacity_by_level"] = s.capacity_by_level
        features.append({"type": "Feature", "geometry": mapping(s.geometry), "properties": props})
    collection = {
        "type": "FeatureCollection",
        "name": "substations_mva",
        "attribution": "(c) OpenStreetMap contributors, ODbL 1.0",
        "osm_snapshot_ts": result.osm_snapshot_ts,
        "features": features,
    }
    paths["substations_geojson"].write_text(
        json.dumps(collection, ensure_ascii=False, default=str), encoding="utf-8"
    )

    with paths["transformers_csv"].open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRANSFORMER_COLUMNS)
        writer.writeheader()
        for t in result.transformers:
            writer.writerow({k: _fmt(v) for k, v in {
                "osm_type": t.osm_type, "osm_id": t.osm_id, "substation_key": t.substation_key,
                "assignment_status": t.assignment_status, "name": t.name, "ref": t.ref,
                "transformer_type": t.transformer_type, "primary_v": t.primary_v,
                "secondary_v": t.secondary_v, "tertiary_v": t.tertiary_v,
                "rating_raw": t.rating_raw, "rating_status": t.rating_status,
                "rating_mva": t.rating_mva, "rating_reject_reason": t.rating_reject_reason,
                "lat": round(t.point.y, 7), "lon": round(t.point.x, 7),
                "quality_flags": sorted(t.quality_flags),
            }.items()})

    with paths["rejects_csv"].open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["element_kind", "osm_type", "osm_id", "reason", "detail", "source_file"])
        for r in result.rejects:
            writer.writerow([r.element_kind, r.osm_type or "", r.osm_id or "", r.reason,
                             r.detail, r.source_file or ""])
    return paths
