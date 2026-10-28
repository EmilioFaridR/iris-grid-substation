from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.config import Settings
from app.runners.process_runner import process, read_manifest

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic"
REAL = FIXTURES / "real"


def make_test_settings(tmp_path: Path | None = None, **overrides) -> Settings:
    base = dict(
        bbox="50.9,6.9,51.6,7.6",
        region_iso="",
        database_url=None,
        check_status_endpoint=False,
        min_request_interval_s=0,
        _env_file=None,
    )
    if tmp_path is not None:
        base.update(data_dir=tmp_path / "data", report_dir=tmp_path / "reports")
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_test_settings(tmp_path)


@pytest.fixture
def synthetic_result(settings):
    return process(read_manifest(SYNTHETIC), SYNTHETIC, settings)


@pytest.fixture
def real_batches():
    out = []
    for name in ("dortmund_substations_center.json", "dortmund_transformers_center.json"):
        payload = json.loads((REAL / name).read_text(encoding="utf-8"))
        out.append((name, payload["elements"]))
    return out


@pytest.fixture
def pg_url() -> str:
    url = os.environ.get("IRIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("IRIS_TEST_DATABASE_URL not set (integration test)")
    return url
