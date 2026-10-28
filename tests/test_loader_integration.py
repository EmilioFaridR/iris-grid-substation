from __future__ import annotations

import copy
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.loaders.staging_loader import UnsafeTargetError, load
from app.runners.process_runner import process, read_manifest
from tests.conftest import SYNTHETIC, make_test_settings

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db(pg_url, tmp_path, monkeypatch):
    schema = f"iris_staging_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("IRIS_DATABASE_URL", pg_url)
    monkeypatch.setenv("IRIS_STAGING_SCHEMA", schema)

    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    settings = make_test_settings(tmp_path, database_url=pg_url, staging_schema=schema)
    engine = create_engine(pg_url, future=True)
    yield settings, engine, schema
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    engine.dispose()


def _count(engine, sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def _synthetic(settings, run_id="run-1", manifest=None):
    manifest = manifest or read_manifest(SYNTHETIC)
    manifest = {**manifest, "run_id": run_id}
    return process(manifest, SYNTHETIC, settings)


def test_migration_creates_staging_tables_only(db):
    _settings, engine, schema = db
    tables = {r[0] for r in engine.connect().execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = :s"), {"s": schema})}
    assert tables == {"alembic_version", "substations_mva", "substation_transformers",
                      "ingest_rejects", "ingest_runs"}


def test_load_and_idempotent_reload(db):
    settings, engine, schema = db
    first = load(_synthetic(settings), settings, engine, {})
    assert first["substations"] == {"inserted": 16}
    assert first["transformers"] == {"inserted": 19}
    assert first["rejects"] == 4

    second = load(_synthetic(settings), settings, engine, {})      # same run again
    assert second["substations"] == {"unchanged": 16}
    assert _count(engine, f'SELECT count(*) FROM "{schema}".substations_mva') == 16
    assert _count(engine, f'SELECT count(*) FROM "{schema}".ingest_rejects') == 4
    assert _count(engine, f'SELECT count(*) FROM "{schema}".ingest_runs') == 1


def test_values_round_trip(db):
    settings, engine, schema = db
    load(_synthetic(settings), settings, engine, {})
    with engine.connect() as conn:
        row = conn.execute(text(
            f"SELECT mva_status, mva_value, mva_level_kv, ST_GeometryType(geom), geom_valid, "
            f"capacity_by_level -> 0 ->> 'rated_mva_sum' "
            f'FROM "{schema}".substations_mva WHERE osm_type = \'way\' AND osm_id = 900007')).one()
    assert row[0] == "inferred_complete" and float(row[1]) == 500 and row[2] == 380
    assert row[3] == "ST_Polygon" and row[4] is True and row[5] == "500"


def test_validation_queries_return_no_violations(db):
    settings, engine, schema = db
    load(_synthetic(settings), settings, engine, {})
    sql = (ROOT / "sql" / "validation_queries.sql").read_text(encoding="utf-8")
    checks_part = sql.split("-- CHECKS (each must return 0 rows)")[1].replace("iris_staging.", f'"{schema}".')
    statements = [s.strip() for s in checks_part.split(";") if "SELECT" in s]
    assert len(statements) == 9
    with engine.connect() as conn:
        for stmt in statements:
            rows = conn.execute(text(stmt)).fetchall()
            assert rows == [], f"validation failed:\n{stmt}\n-> {rows}"


def test_changed_and_vanished_rows_on_next_complete_run(db):
    settings, engine, schema = db
    load(_synthetic(settings, "run-1"), settings, engine, {})

    # Next weekly run: Beta gets a new name, Omikron disappeared from OSM.
    import json
    import shutil

    workdir = settings.data_dir / "run2"
    shutil.copytree(SYNTHETIC, workdir)
    tile = json.loads((workdir / "tile_a.json").read_text())
    for el in tile["elements"]:
        if el.get("id") == 900002:
            el["tags"]["name"] = "Station Beta Neu"
    tile["elements"] = [e for e in tile["elements"] if e.get("id") != 900014]
    (workdir / "tile_a.json").write_text(json.dumps(tile))
    manifest = copy.deepcopy(read_manifest(SYNTHETIC))
    for t in manifest["tiles"]:
        t.pop("sha256", None)
    manifest["run_id"] = "run-2"
    result = process(manifest, workdir, settings)

    stats = load(result, settings, engine, {})
    assert stats["substations"]["updated"] >= 1
    assert stats["marked_absent"]["substations"] == 1
    with engine.connect() as conn:
        beta = conn.execute(text(
            f'SELECT name, first_seen_run_id, last_seen_run_id FROM "{schema}".substations_mva '
            "WHERE osm_id = 900002")).one()
        omikron = conn.execute(text(
            f'SELECT present_in_latest_snapshot FROM "{schema}".substations_mva WHERE osm_id = 900014')).scalar()
    assert beta == ("Station Beta Neu", "run-1", "run-2")
    assert omikron is False      # kept, not deleted


def test_partial_run_never_marks_rows_absent(db):
    settings, engine, _schema = db
    load(_synthetic(settings, "run-1"), settings, engine, {})
    manifest = read_manifest(SYNTHETIC)
    manifest = {**manifest, "status": "partial", "tiles": manifest["tiles"][1:]}   # only tile_b
    stats = load(_synthetic(settings, "run-2", manifest), settings, engine, {})
    assert stats["marked_absent"] == {"substations": 0, "transformers": 0}


def test_loader_refuses_iris_core_even_if_settings_are_forged(db):
    settings, engine, _schema = db
    forged = settings.model_copy(update={"staging_schema": "iris_core"})
    with pytest.raises(UnsafeTargetError):
        load(_synthetic(settings), forged, engine, {})
