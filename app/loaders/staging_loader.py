from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal

from geoalchemy2.elements import WKTElement
from sqlalchemy import and_, case, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from app.config import FORBIDDEN_SCHEMAS, Settings
from app.database.tables import StagingTables, build_tables
from app.logging_config import get_logger
from app.models import SubstationRecord, TransformerRecord
from app.runners.process_runner import ProcessResult

BATCH_SIZE = 1000


class UnsafeTargetError(RuntimeError):
    pass


def _assert_staging(schema: str) -> None:
    if schema.lower() in FORBIDDEN_SCHEMAS or not schema.lower().startswith("iris_staging"):
        raise UnsafeTargetError(f"refusing to write to schema {schema!r}")


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _wkt(geom) -> WKTElement | None:
    return None if geom is None else WKTElement(geom.wkt, srid=4326)


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value))


def _hash(row: dict) -> str:
    stable = {k: (v.data if isinstance(v, WKTElement) else v) for k, v in row.items()}
    blob = json.dumps(stable, sort_keys=True, default=_json_default, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Record -> row


def substation_row(s: SubstationRecord) -> dict:
    row = {
        "osm_type": s.osm_type,
        "osm_id": s.osm_id,
        "name": s.name,
        "operators": s.operators,
        "operator_wikidata": s.operator_wikidata,
        "wikidata": s.wikidata,
        "substation_type": s.substation_type,
        "lifecycle": s.lifecycle,
        "frequency_hz": s.frequency_hz,
        "voltage_raw": s.voltage_raw,
        "voltages_v": s.voltages_v,
        "hv_levels_kv": s.hv_levels_kv,
        "max_voltage_kv": s.max_voltage_kv,
        "rating_raw": s.rating_raw,
        "rating_status": s.rating_status,
        "rating_reject_reason": s.rating_reject_reason,
        "mva_status": s.mva_status,
        "mva_value": s.mva_value,
        "mva_level_kv": s.mva_level_kv,
        "mva_is_lower_bound": s.mva_is_lower_bound,
        "mva_unknown_reason": s.mva_unknown_reason,
        "capacity_by_level": s.capacity_by_level,
        "transformer_count": s.transformer_count,
        "transformer_rated_count": s.transformer_rated_count,
        "geom": _wkt(s.geometry),
        "centroid": _wkt(s.centroid),
        "geom_kind": s.geom_kind,
        "geom_valid": s.geom_valid,
        "area_m2": None if s.area_m2 is None else round(s.area_m2, 1),
        "is_grid_candidate": s.is_grid_candidate,
        "non_grid_reason": s.non_grid_reason,
        "is_duplicate": s.is_duplicate,
        "duplicate_of": s.duplicate_of,
        "site_id": s.site_id,
        "site_group_reason": s.site_group_reason,
        "quality_flags": sorted(s.quality_flags),
        "tags": s.tags,
    }
    row["content_hash"] = _hash(row)
    row["source_file"] = s.source_file
    return row


def transformer_row(t: TransformerRecord) -> dict:
    sub_type, sub_id = (t.substation_key.split("/") if t.substation_key else (None, None))
    row = {
        "osm_type": t.osm_type,
        "osm_id": t.osm_id,
        "substation_osm_type": sub_type,
        "substation_osm_id": int(sub_id) if sub_id else None,
        "assignment_status": t.assignment_status,
        "name": t.name,
        "ref": t.ref,
        "transformer_type": t.transformer_type,
        "frequency_hz": t.frequency_hz,
        "primary_v": t.primary_v,
        "secondary_v": t.secondary_v,
        "tertiary_v": t.tertiary_v,
        "rating_raw": t.rating_raw,
        "rating_status": t.rating_status,
        "rating_mva": t.rating_mva,
        "rating_reject_reason": t.rating_reject_reason,
        "geom": _wkt(t.point),
        "quality_flags": sorted(t.quality_flags),
        "tags": t.tags,
    }
    row["content_hash"] = _hash(row)
    row["source_file"] = t.source_file
    return row


# Upsert


def _upsert(conn: Connection, table, rows: list[dict], run_id: str, snapshot) -> Counter:
    counts: Counter = Counter()
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        keys = [(r["osm_type"], r["osm_id"]) for r in batch]
        existing = dict(
            ((t, i), h) for t, i, h in conn.execute(
                select(table.c.osm_type, table.c.osm_id, table.c.content_hash)
                .where(tuple_(table.c.osm_type, table.c.osm_id).in_(keys))
            )
        )
        for r in batch:
            old = existing.get((r["osm_type"], r["osm_id"]))
            counts["inserted" if old is None else ("unchanged" if old == r["content_hash"] else "updated")] += 1
            r.update(
                osm_snapshot_ts=snapshot,
                first_seen_run_id=run_id,
                last_seen_run_id=run_id,
                present_in_latest_snapshot=True,
            )

        stmt = pg_insert(table).values(batch)
        excluded = stmt.excluded
        changed = table.c.content_hash.is_distinct_from(excluded.content_hash)
        update_cols = {
            c.name: excluded[c.name]
            for c in table.columns
            if c.name not in ("osm_type", "osm_id", "first_seen_run_id", "updated_at")
        }
        # keep the time of the last *content* change, not of the last load
        update_cols["updated_at"] = case((changed, func.now()), else_=table.c.updated_at)
        conn.execute(stmt.on_conflict_do_update(index_elements=["osm_type", "osm_id"], set_=update_cols))
    return counts


def _mark_absent(conn: Connection, table, run_id: str, bbox: tuple[float, float, float, float],
                 point_column: str) -> int:
    south, west, north, east = bbox
    envelope = func.ST_MakeEnvelope(west, south, east, north, 4326)
    result = conn.execute(
        update(table)
        .where(and_(
            table.c.last_seen_run_id != run_id,
            table.c.present_in_latest_snapshot.is_(True),
            func.ST_Intersects(table.c[point_column], envelope),
        ))
        .values(present_in_latest_snapshot=False)
    )
    return result.rowcount or 0


def load(result: ProcessResult, settings: Settings, engine: Engine, stats_for_run: dict) -> dict:
    schema = settings.staging_schema
    _assert_staging(schema)
    tables: StagingTables = build_tables(schema)
    log = get_logger("loader", run_id=result.run_id)
    snapshot = _ts(result.osm_snapshot_ts)
    manifest = result.manifest

    sub_rows = [substation_row(s) for s in result.substations]
    trafo_rows = [transformer_row(t) for t in result.transformers]
    reject_rows = [
        {
            "run_id": result.run_id,
            "element_kind": r.element_kind,
            "osm_type": r.osm_type,
            "osm_id": r.osm_id,
            "reason": r.reason,
            "detail": r.detail,
            "raw": r.raw,
            "source_file": r.source_file,
        }
        for r in result.rejects
    ]
    tiles = manifest.get("tiles", [])
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode("utf-8")
    ).hexdigest()

    with engine.begin() as conn:
        sub_counts = _upsert(conn, tables.substations, sub_rows, result.run_id, snapshot)
        trafo_counts = _upsert(conn, tables.transformers, trafo_rows, result.run_id, snapshot)
        if reject_rows:
            conn.execute(
                pg_insert(tables.rejects).values(reject_rows).on_conflict_do_nothing(
                    constraint="uq_ingest_rejects_run_element_reason"
                )
            )

        absent = {"substations": 0, "transformers": 0}
        bbox = manifest.get("bbox")
        if result.is_complete and bbox:
            box = tuple(float(v) for v in bbox.split(","))
            absent["substations"] = _mark_absent(conn, tables.substations, result.run_id, box, "centroid")
            absent["transformers"] = _mark_absent(conn, tables.transformers, result.run_id, box, "geom")

        load_stats = {
            "substations": dict(sub_counts),
            "transformers": dict(trafo_counts),
            "rejects": len(reject_rows),
            "marked_absent": absent,
        }
        run_row = {
            "run_id": result.run_id,
            "status": manifest.get("status", "success"),
            "source_endpoint": manifest.get("endpoint"),
            "bbox": manifest.get("bbox"),
            "region_iso": manifest.get("region_iso"),
            "osm_snapshot_ts": snapshot,
            "areas_snapshot_ts": _ts(result.areas_snapshot_ts),
            "tiles_total": len(tiles),
            "tiles_ok": sum(t.get("status") == "ok" for t in tiles),
            "tiles_failed": sum(t.get("status") != "ok" for t in tiles),
            "raw_manifest_sha256": manifest_hash,
            "stats": {**stats_for_run, "load": load_stats},
        }
        stmt = pg_insert(tables.runs).values(run_row)
        conn.execute(stmt.on_conflict_do_update(
            index_elements=["run_id"],
            set_={k: stmt.excluded[k] for k in run_row if k != "run_id"},
        ))

    log.info(f"loaded into {schema}: substations={dict(sub_counts)} transformers={dict(trafo_counts)} "
             f"rejects={len(reject_rows)} absent={absent}")
    return load_stats


def record_failed_run(settings: Settings, engine: Engine, run_id: str, error: str) -> None:
    _assert_staging(settings.staging_schema)
    tables = build_tables(settings.staging_schema)
    with engine.begin() as conn:
        stmt = pg_insert(tables.runs).values(run_id=run_id, status="failed", error=error[:4000])
        conn.execute(stmt.on_conflict_do_update(
            index_elements=["run_id"], set_={"status": "failed", "error": stmt.excluded.error}
        ))
