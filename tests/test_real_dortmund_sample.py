from collections import Counter
from decimal import Decimal

from app.models import MVA_UNKNOWN
from app.parsers.element_parser import parse_elements
from app.transformers.assignment import assign_transformers
from app.transformers.capacity import compute_all
from app.transformers.dedup import mark_duplicates
from app.transformers.sites import assign_sites


def _parse(real_batches, settings):
    return parse_elements(real_batches, settings)


def test_all_real_elements_are_accounted_for(real_batches, settings):
    res = _parse(real_batches, settings)
    assert res.stats["elements_received"] == 33 + 56
    assert len(res.substations) == 33
    assert len(res.transformers) == 56
    assert res.rejects == []


def test_real_voltage_distribution(real_batches, settings):
    res = _parse(real_batches, settings)
    raw = Counter(s.voltage_raw for s in res.substations.values())
    assert raw == {"110000": 23, "110000;15000": 3, "380000": 3, "220000;110000": 2, "220000": 2}
    wambel = res.substations["way/24414688"]
    assert wambel.name == "Station Wambel"
    assert wambel.hv_levels_kv == [220, 110] and wambel.max_voltage_kv == 220


def test_real_ratings(real_batches, settings):
    res = _parse(real_batches, settings)
    assert all(s.rating_status == "missing" for s in res.substations.values())  # 0 of 33
    rated = [t for t in res.transformers.values() if t.rating_raw]
    assert len(rated) == 6
    parsed = {t.key: t.rating_mva for t in rated if t.rating_status == "parsed"}
    assert parsed == {"node/667446938": Decimal(40), "node/667446939": Decimal(40), "node/667446940": Decimal(40)}
    rejected = [t for t in rated if t.rating_status == "rejected"]
    assert len(rejected) == 3
    assert {t.rating_reject_reason for t in rejected} == {"active_power_unit_not_mva"}   # "10 MW" (DB Energie)


def test_real_transformer_voltage_problems_are_flagged(real_batches, settings):
    res = _parse(real_batches, settings)
    trafos = res.transformers.values()
    assert sum("voltage_secondary_unparseable" in t.quality_flags for t in trafos) == 3   # "medium"
    no_primary = [t for t in trafos if t.primary_v is None]
    assert len(no_primary) == 6
    assert all("missing_primary_voltage" in t.quality_flags for t in no_primary)


def test_real_non_grid_classification(real_batches, settings):
    res = _parse(real_batches, settings)
    reasons = Counter(s.non_grid_reason for s in res.substations.values() if not s.is_grid_candidate)
    assert reasons == {"type:traction": 3, "type:transition": 2, "lifecycle:disused": 1}


def test_center_only_substations_never_get_a_capacity(real_batches, settings):
    res = _parse(real_batches, settings)
    subs, trafos = list(res.substations.values()), list(res.transformers.values())
    assert {s.geom_kind for s in subs} == {"center_only"}
    mark_duplicates(subs)
    assignment = assign_transformers(subs, trafos)
    assert assignment == {"unassigned": 56}
    compute_all(subs, trafos)
    assert all(s.mva_status == MVA_UNKNOWN and s.mva_value is None for s in subs)
    assert {s.mva_unknown_reason for s in subs} == {"no_polygon_geometry"}


def test_real_sites(real_batches, settings):
    res = _parse(real_batches, settings)
    subs = list(res.substations.values())
    assign_sites(subs)
    by_key = {s.key: s for s in subs}

    def same_site(a, b):
        return by_key[a].site_id == by_key[b].site_id

    assert same_site("way/22964137", "way/1460084299")    # Station Kruckel 110 kV + 380 kV
    assert same_site("way/24667346", "way/1457106049")    # Garenfeld 220 + 110, same wikidata
    assert same_site("way/35763480", "way/35763481")      # Witten / Station Witten
    assert "same_wikidata" in by_key["way/24667346"].site_group_reason
    # DB traction "Unterwerk Dortmund" and public "Dorstfeld" are ~150 m apart: separate sites
    assert not same_site("way/52698907", "way/52705458")
