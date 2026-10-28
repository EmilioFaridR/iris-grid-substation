from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.logging_config import get_logger
from app.scrapers.overpass.client import OverpassClient, OverpassError
from app.scrapers.overpass.queries import build_tile_query, make_tiles

MANIFEST = "manifest.json"


@dataclass
class FetchSummary:
    run_id: str
    raw_dir: Path
    status: str
    tiles_total: int
    tiles_ok: int
    tiles_failed: int


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_fetch(settings: Settings, run_id: str, client: OverpassClient | None = None) -> FetchSummary:
    log = get_logger("overpass", run_id=run_id)
    client = client or OverpassClient(settings, run_id=run_id)
    raw_dir = settings.raw_dir / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)

    tiles = make_tiles(settings.bbox_tuple, settings.tile_deg)
    log.info(f"fetching {len(tiles)} tiles for bbox {settings.bbox} region={settings.region_iso or '-'}")
    manifest = {
        "run_id": run_id,
        "source": "OpenStreetMap via Overpass API",
        "license": "ODbL 1.0 - (c) OpenStreetMap contributors",
        "endpoint": settings.overpass_url,
        "bbox": settings.bbox,
        "region_iso": settings.region_iso or None,
        "tile_deg": settings.tile_deg,
        "target_voltages_kv": sorted(settings.target_voltages),
        "started_at": _utcnow(),
        "tiles": [],
    }

    ok = failed = 0
    for tile in tiles:
        query = build_tile_query(tile.bbox, settings.target_voltages,
                                 settings.query_timeout_s, settings.region_iso or None)
        entry = {"tile_id": tile.tile_id, "bbox": list(tile.bbox), "query": query,
                 "fetched_at": _utcnow()}
        try:
            resp = client.query(query, label=f"tile {tile.tile_id}")
        except OverpassError as exc:
            failed += 1
            entry.update(status="failed", error=str(exc))
            log.error(f"tile {tile.tile_id} failed: {exc}")
        else:
            ok += 1
            filename = f"tile_{tile.tile_id}.json"
            (raw_dir / filename).write_bytes(resp.raw_bytes)
            osm3s = resp.payload.get("osm3s", {})
            entry.update(
                status="ok",
                file=filename,
                sha256=sha256_bytes(resp.raw_bytes),
                bytes=len(resp.raw_bytes),
                http_status=resp.http_status,
                attempts=resp.attempts,
                elapsed_s=round(resp.elapsed_s, 2),
                element_count=len(resp.payload.get("elements", [])),
                timestamp_osm_base=osm3s.get("timestamp_osm_base"),
                timestamp_areas_base=osm3s.get("timestamp_areas_base"),
                remarks=resp.remarks,
            )
        manifest["tiles"].append(entry)
        # write after every tile so an interrupted run keeps what it fetched
        (raw_dir / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    status = "success" if failed == 0 else ("partial" if ok else "failed")
    manifest.update(
        finished_at=_utcnow(),
        status=status,
        client_stats={
            "requests": client.stats.requests,
            "retries": client.stats.retries,
            "status_checks": client.stats.status_checks,
            "waited_s": round(client.stats.waited_s, 1),
            "failures_by_reason": client.stats.failures_by_reason,
        },
    )
    (raw_dir / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info(f"fetch {status}: {ok}/{len(tiles)} tiles ok, raw data in {raw_dir}")
    return FetchSummary(run_id, raw_dir, status, len(tiles), ok, failed)


def manifest_for_files(paths: list[Path], run_id: str) -> dict:
    """Build an in-memory manifest for Overpass JSON files saved by hand
    (e.g. exported from overpass-turbo), so they go through the same pipeline."""
    tiles = []
    for i, path in enumerate(paths):
        data = path.read_bytes()
        payload = json.loads(data)
        osm3s = payload.get("osm3s", {})
        tiles.append({
            "tile_id": f"file{i:02d}",
            "status": "ok",
            "file": str(path.resolve()),
            "sha256": sha256_bytes(data),
            "bytes": len(data),
            "element_count": len(payload.get("elements", [])),
            "timestamp_osm_base": osm3s.get("timestamp_osm_base"),
            "timestamp_areas_base": osm3s.get("timestamp_areas_base"),
        })
    return {
        "run_id": run_id,
        "source": "OpenStreetMap via Overpass API (file import)",
        "license": "ODbL 1.0 - (c) OpenStreetMap contributors",
        "endpoint": None,
        "bbox": None,      # unknown scope -> rows are never marked absent from such a run
        "region_iso": None,
        "status": "success",
        "tiles": tiles,
    }
