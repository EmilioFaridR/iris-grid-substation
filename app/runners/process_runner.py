from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.logging_config import get_logger
from app.models import Reject, SubstationRecord, TransformerRecord
from app.parsers.element_parser import parse_elements
from app.scrapers.overpass.substation_scraper import MANIFEST, sha256_bytes
from app.transformers.assignment import assign_transformers
from app.transformers.capacity import compute_all
from app.transformers.dedup import mark_duplicates
from app.transformers.sites import assign_sites


class RawIntegrityError(RuntimeError):
    """A raw file does not match the sha256 recorded when it was fetched."""


@dataclass
class ProcessResult:
    run_id: str
    manifest: dict
    substations: list[SubstationRecord]
    transformers: list[TransformerRecord]
    rejects: list[Reject]
    stats: dict[str, Counter] = field(default_factory=dict)

    @property
    def osm_snapshot_ts(self) -> str | None:
        stamps = [t.get("timestamp_osm_base") for t in self.manifest.get("tiles", [])
                  if t.get("status") == "ok" and t.get("timestamp_osm_base")]
        return min(stamps) if stamps else None

    @property
    def areas_snapshot_ts(self) -> str | None:
        stamps = [t.get("timestamp_areas_base") for t in self.manifest.get("tiles", [])
                  if t.get("status") == "ok" and t.get("timestamp_areas_base")]
        return min(stamps) if stamps else None

    @property
    def is_complete(self) -> bool:
        return self.manifest.get("status") == "success"


def read_manifest(raw_dir: Path) -> dict:
    return json.loads((raw_dir / MANIFEST).read_text(encoding="utf-8"))


def load_batches(manifest: dict, raw_dir: Path | None) -> list[tuple[str, list[dict]]]:
    batches: list[tuple[str, list[dict]]] = []
    for tile in manifest.get("tiles", []):
        if tile.get("status") != "ok":
            continue
        path = Path(tile["file"])
        if not path.is_absolute() and raw_dir is not None:
            path = raw_dir / path
        data = path.read_bytes()
        expected = tile.get("sha256")
        if expected and sha256_bytes(data) != expected:
            raise RawIntegrityError(f"{path} sha256 mismatch - raw file was modified")
        payload = json.loads(data)
        batches.append((path.name, payload.get("elements", [])))
    return batches


def process(manifest: dict, raw_dir: Path | None, settings: Settings) -> ProcessResult:
    run_id = manifest.get("run_id", "-")
    log = get_logger("process", run_id=run_id)

    parsed = parse_elements(load_batches(manifest, raw_dir), settings)
    substations = sorted(parsed.substations.values(), key=lambda s: s.key)
    transformers = sorted(parsed.transformers.values(), key=lambda t: t.key)

    duplicates = mark_duplicates(substations)
    assignment = assign_transformers(substations, transformers)
    capacity = compute_all(substations, transformers)
    sites = assign_sites(
        substations,
        name_max_distance_m=settings.site_name_max_distance_m,
        adjacency_max_distance_m=settings.site_adjacency_max_distance_m,
    )

    result = ProcessResult(
        run_id=run_id,
        manifest=manifest,
        substations=substations,
        transformers=transformers,
        rejects=parsed.rejects,
        stats={
            "parse": parsed.stats,
            "dedup": Counter({"duplicates_marked": duplicates}),
            "assignment": assignment,
            "capacity": capacity,
            "sites": sites,
        },
    )
    log.info(
        f"processed {len(substations)} substations, {len(transformers)} transformers, "
        f"{len(parsed.rejects)} rejects; mva_status={dict(capacity)}"
    )
    return result
