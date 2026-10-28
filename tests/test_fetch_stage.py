import json

import responses

from app.runners.process_runner import process, read_manifest
from app.scrapers.overpass.client import OverpassClient
from app.scrapers.overpass.substation_scraper import run_fetch, sha256_bytes
from tests.conftest import SYNTHETIC, make_test_settings

URL = "https://overpass-api.de/api/interpreter"


def _client(settings):
    return OverpassClient(settings, sleep=lambda s: None, clock=lambda: 0.0, run_id="t")


@responses.activate
def test_fetch_writes_raw_and_manifest_and_can_be_reprocessed(tmp_path):
    settings = make_test_settings(tmp_path, bbox="50.9,6.9,51.3,7.1", tile_deg=0.2, max_retries=0)
    body = (SYNTHETIC / "tile_a.json").read_bytes()
    responses.add(responses.POST, URL, body=body, status=200)          # tile 1
    responses.add(responses.POST, URL, body=b'{"elements": []}', status=200)  # tile 2

    summary = run_fetch(settings, "run-x", _client(settings))
    assert (summary.status, summary.tiles_total, summary.tiles_ok) == ("success", 2, 2)

    manifest = read_manifest(summary.raw_dir)
    first = manifest["tiles"][0]
    assert (summary.raw_dir / first["file"]).read_bytes() == body          # stored untouched
    assert first["sha256"] == sha256_bytes(body)
    assert first["timestamp_osm_base"] == "2026-10-27T18:00:00Z"
    assert "out geom" in first["query"]
    assert manifest["license"].startswith("ODbL")

    result = process(manifest, summary.raw_dir, settings)
    assert len(result.substations) == 16 and result.is_complete


@responses.activate
def test_failed_tile_makes_run_partial_but_keeps_other_tiles(tmp_path):
    settings = make_test_settings(tmp_path, bbox="50.9,6.9,51.3,7.1", tile_deg=0.2, max_retries=1)
    responses.add(responses.POST, URL, body=(SYNTHETIC / "tile_b.json").read_bytes(), status=200)
    responses.add(responses.POST, URL, status=504)
    responses.add(responses.POST, URL, status=504)

    summary = run_fetch(settings, "run-y", _client(settings))
    assert (summary.status, summary.tiles_ok, summary.tiles_failed) == ("partial", 1, 1)
    manifest = json.loads((summary.raw_dir / "manifest.json").read_text())
    failed = [t for t in manifest["tiles"] if t["status"] == "failed"]
    assert failed and "504" in failed[0]["error"]
    assert manifest["client_stats"]["failures_by_reason"] == {"http_504": 2}

    result = process(manifest, summary.raw_dir, settings)
    assert not result.is_complete and len(result.substations) == 1
