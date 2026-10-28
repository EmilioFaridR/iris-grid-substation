import pytest
from pydantic import ValidationError

from app.config import NRW_BBOX
from app.scrapers.overpass.queries import build_tile_query, make_tiles
from tests.conftest import make_test_settings


@pytest.mark.parametrize("schema", ["iris_core", "IRIS_CORE", "public", "analytics"])
def test_settings_refuse_non_staging_schema(schema):
    with pytest.raises(ValidationError):
        make_test_settings(staging_schema=schema)


def test_settings_accept_staging_schemas():
    assert make_test_settings(staging_schema="iris_staging").staging_schema == "iris_staging"
    assert make_test_settings(staging_schema="iris_staging_test").staging_schema == "iris_staging_test"


def test_settings_refuse_unauthorised_endpoint():
    with pytest.raises(ValidationError):
        make_test_settings(overpass_url="https://my-scraper-proxy.example.com/api/interpreter")


@pytest.mark.parametrize("bbox", ["1,2,3", "52,7,51,8", "51,7,52,200"])
def test_invalid_bbox(bbox):
    with pytest.raises(ValueError):
        _ = make_test_settings(bbox=bbox).bbox_tuple


def test_tiles_cover_bbox_exactly_and_deterministically():
    tiles = make_tiles(NRW_BBOX, 1.0)
    assert len(tiles) == 3 * 4
    assert tiles == make_tiles(NRW_BBOX, 1.0)
    assert min(t.south for t in tiles) == NRW_BBOX[0] and max(t.north for t in tiles) == NRW_BBOX[2]
    assert min(t.west for t in tiles) == NRW_BBOX[1] and max(t.east for t in tiles) == NRW_BBOX[3]
    area = sum((t.north - t.south) * (t.east - t.west) for t in tiles)
    expected = (NRW_BBOX[2] - NRW_BBOX[0]) * (NRW_BBOX[3] - NRW_BBOX[1])
    assert area == pytest.approx(expected)
    assert len({t.tile_id for t in tiles}) == len(tiles)


def test_query_shape():
    q = build_tile_query((51.4, 7.3, 51.55, 7.55), {110, 220, 380}, 180, "DE-NW")
    assert q.startswith("[out:json][timeout:180];")
    assert 'area["ISO3166-2"="DE-NW"]' in q
    assert '["voltage"~"110|220|380"](area.region)(51.4,7.3,51.55,7.55)' in q
    assert ".subs out geom;" in q and ".trafos out center;" in q


def test_query_without_region():
    q = build_tile_query((51.4, 7.3, 51.55, 7.55), {110}, 60, None)
    assert "area.region" not in q and "(51.4,7.3,51.55,7.55)" in q


@pytest.mark.parametrize("bad", ['DE-NW"];out;', "de-nw", "DE NW"])
def test_region_code_is_validated_against_injection(bad):
    with pytest.raises(ValueError):
        build_tile_query((51.4, 7.3, 51.55, 7.55), {110}, 60, bad)
