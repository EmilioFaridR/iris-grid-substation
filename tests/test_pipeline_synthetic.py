import json
import shutil
from decimal import Decimal

import pytest

from app.exporters.files import export
from app.models import (
    MVA_INFERRED_COMPLETE,
    MVA_INFERRED_PARTIAL,
    MVA_OBSERVED,
    MVA_UNKNOWN,
)
from app.reports.quality_report import build_summary, verify_capacity_evidence, write_report
from app.runners.process_runner import RawIntegrityError, process, read_manifest
from tests.conftest import SYNTHETIC


def by_key(result):
    return {s.key: s for s in result.substations}


EXPECTED = {
    # key: (mva_status, mva_value, lower_bound, unknown_reason)
    "way/900001": (MVA_UNKNOWN, None, False, "transformers_unrated"),        # Wambel pattern
    "way/900002": (MVA_INFERRED_COMPLETE, Decimal(80), False, None),
    "node/900101": (MVA_UNKNOWN, None, False, "duplicate_of_other_substation"),
    "way/900003": (MVA_INFERRED_PARTIAL, Decimal("31.5"), True, None),
    "way/900004": (MVA_UNKNOWN, None, False, "transformer_rating_rejected"),   # 10 MW
    "way/900005": (MVA_OBSERVED, Decimal(300), False, None),
    "way/900006": (MVA_UNKNOWN, None, False, "transformers_unrated"),
    "way/900007": (MVA_INFERRED_COMPLETE, Decimal(500), False, None),         # 500000 kVA
    "way/900008": (MVA_UNKNOWN, None, False, "no_polygon_geometry"),          # unclosed way
    "way/900009": (MVA_INFERRED_COMPLETE, Decimal(63), False, None),          # invalid polygon, still usable
    "relation/900501": (MVA_INFERRED_COMPLETE, Decimal(800), False, None),    # 2x400 MVA
    "way/900010": (MVA_UNKNOWN, None, False, "no_transformers_mapped"),
    "way/900012": (MVA_UNKNOWN, None, False, "transformers_unrated"),
    "node/900102": (MVA_UNKNOWN, None, False, "no_polygon_geometry"),
    "way/900013": (MVA_UNKNOWN, None, False, "no_transformers_mapped"),
    "way/900014": (MVA_UNKNOWN, None, False, "no_transformers_mapped"),
}


def test_capacity_per_substation(synthetic_result):
    subs = by_key(synthetic_result)
    assert set(subs) == set(EXPECTED)
    for key, (status, value, lower, reason) in EXPECTED.items():
        s = subs[key]
        assert (s.mva_status, s.mva_value, s.mva_is_lower_bound, s.mva_unknown_reason) == (
            status, value, lower, reason), key


def test_no_fabricated_capacity(synthetic_result):
    assert verify_capacity_evidence(synthetic_result) == []


def test_lower_level_ratings_are_reported_but_not_summed(synthetic_result):
    alpha = by_key(synthetic_result)["way/900001"]
    assert "rated_capacity_only_at_lower_level" in alpha.quality_flags
    levels = {(lv["primary_kv"], lv["secondary_kv"]): lv for lv in alpha.capacity_by_level}
    assert levels[(110, 10)]["rated_mva_sum"] == "120" and levels[(110, 10)]["complete"]
    assert levels[(220, 110)]["rated_count"] == 0
    assert alpha.mva_value is None


def test_observed_rating_wins_and_mismatch_is_flagged(synthetic_result):
    eps = by_key(synthetic_result)["way/900005"]
    assert eps.mva_status == MVA_OBSERVED
    assert "observed_differs_from_transformer_sum" in eps.quality_flags


def test_duplicates(synthetic_result):
    subs = by_key(synthetic_result)
    assert subs["node/900101"].is_duplicate and subs["node/900101"].duplicate_of == "way/900002"
    assert subs["node/900101"].site_id == subs["way/900002"].site_id
    assert synthetic_result.stats["parse"]["tile_overlap_duplicates"] == 3   # tile_b repeats 3 elements


def test_transformer_assignment(synthetic_result):
    trafos = {t.key: t for t in synthetic_result.transformers}
    assert trafos["node/910051"].substation_key == "way/900007"   # 380 kV yard, not the 110 kV one
    assert trafos["node/910052"].substation_key == "way/900006"
    assert trafos["node/910061"].substation_key == "way/900009"   # inside the repaired bow-tie
    assert trafos["node/910071"].substation_key == "relation/900501"
    assert trafos["node/910081"].assignment_status == "unassigned"
    assert synthetic_result.stats["assignment"] == {"contained": 18, "unassigned": 1}


def test_sites(synthetic_result):
    subs = by_key(synthetic_result)
    assert subs["way/900006"].site_id == subs["way/900007"].site_id == "way/900006"
    assert subs["way/900006"].site_group_reason.startswith("same_name_within")
    assert subs["relation/900501"].site_id == subs["way/900010"].site_id
    assert subs["way/900010"].site_group_reason == "same_wikidata"
    # traction yard is adjacent (~7 m) to a public substation but must not be merged
    assert subs["way/900004"].site_id != subs["way/900014"].site_id


def test_classification_and_geometry_flags(synthetic_result):
    subs = by_key(synthetic_result)
    assert not subs["way/900004"].is_grid_candidate and subs["way/900004"].non_grid_reason == "type:traction"
    assert not subs["way/900013"].is_grid_candidate and subs["way/900013"].non_grid_reason == "lifecycle:disused"
    assert subs["way/900009"].geom_valid is False
    assert "unclosed_way" in subs["way/900008"].quality_flags
    assert "voltage_unitless_kv_assumed" in subs["node/900102"].quality_flags


def test_rejects_are_kept_with_reason(synthetic_result):
    reasons = {(r.element_kind, r.key, r.reason) for r in synthetic_result.rejects}
    assert reasons == {
        ("substation", "node/900103", "coordinates_out_of_range"),
        ("substation", "way/900011", "no_target_voltage"),
        ("element", "way/900900", "unexpected_power_tag"),
        ("element", "None/None", "malformed_element"),
    }
    assert all(r.raw for r in synthetic_result.rejects)   # raw JSON preserved


def test_processing_is_deterministic(settings, tmp_path):
    outputs = []
    for i in range(2):
        result = process(read_manifest(SYNTHETIC), SYNTHETIC, settings)
        paths = export(result, tmp_path / f"out{i}")
        outputs.append({k: p.read_bytes() for k, p in paths.items()})
    assert outputs[0] == outputs[1]


def test_tampered_raw_file_is_detected(settings, tmp_path):
    copy = tmp_path / "raw"
    shutil.copytree(SYNTHETIC, copy)
    data = json.loads((copy / "tile_a.json").read_text())
    data["elements"][0]["tags"]["rating"] = "999 MVA"
    (copy / "tile_a.json").write_text(json.dumps(data))
    with pytest.raises(RawIntegrityError):
        process(read_manifest(copy), copy, settings)


def test_report_and_exports(synthetic_result, tmp_path):
    md, js = write_report(synthetic_result, tmp_path / "reports")
    text = md.read_text()
    assert "No fabricated capacity" in text and "| PASS |" in text
    summary = json.loads(js.read_text())
    cap = summary["capacity_grid_candidates"]
    assert summary["evidence_violations"] == []
    assert cap["by_status"] == {"observed": 1, "inferred_complete": 4, "inferred_partial": 1, "unknown": 7}
    paths = export(synthetic_result, tmp_path / "out")
    geo = json.loads(paths["substations_geojson"].read_text())
    assert len(geo["features"]) == 16 and "OpenStreetMap" in geo["attribution"]
    assert paths["substations_csv"].read_text().count("\n") == 17


def test_summary_counts_unique_candidates_only(synthetic_result):
    summary = build_summary(synthetic_result)
    assert summary["substations"]["duplicates_flagged"] == 1
    assert summary["substations"]["grid_candidates"] == 13   # 15 unique - traction - disused
