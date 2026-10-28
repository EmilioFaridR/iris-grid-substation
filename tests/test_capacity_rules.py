from decimal import Decimal

from shapely.geometry import Point, box

from app.models import (
    MVA_INFERRED_COMPLETE,
    MVA_INFERRED_PARTIAL,
    MVA_OBSERVED,
    MVA_UNKNOWN,
    SubstationRecord,
    TransformerRecord,
)
from app.transformers.capacity import compute_capacity


def sub(max_kv=110, rating=None):
    s = SubstationRecord("way", 1, {}, None, max_voltage_kv=max_kv, hv_levels_kv=[max_kv])
    s.geometry, s.geom_kind, s.centroid = box(7.0, 51.0, 7.001, 51.001), "polygon", Point(7.0005, 51.0005)
    if rating is not None:
        s.rating_status, s.rating_mva = "parsed", Decimal(rating)
    return s


def trafo(i, primary=110, secondary=10, mva=None, status=None, flags=()):
    t = TransformerRecord("node", i, {}, None, primary_v=primary and primary * 1000,
                          secondary_v=secondary and secondary * 1000, point=Point(7.0005, 51.0005))
    if mva is not None:
        t.rating_status, t.rating_mva = "parsed", Decimal(mva)
    elif status:
        t.rating_status = status
    t.quality_flags = list(flags)
    return t


def test_all_top_level_rated_is_complete():
    s = sub()
    compute_capacity(s, [trafo(1, mva=40), trafo(2, mva=40)])
    assert (s.mva_status, s.mva_value, s.mva_is_lower_bound) == (MVA_INFERRED_COMPLETE, Decimal(80), False)


def test_some_rated_is_partial_lower_bound():
    s = sub()
    compute_capacity(s, [trafo(1, mva=40), trafo(2)])
    assert (s.mva_status, s.mva_value, s.mva_is_lower_bound) == (MVA_INFERRED_PARTIAL, Decimal(40), True)


def test_levels_are_never_summed_together():
    s = sub(max_kv=380)
    compute_capacity(s, [trafo(1, 380, 110, mva=300), trafo(2, 110, 10, mva=40)])
    assert s.mva_value == Decimal(300) and s.mva_level_kv == 380


def test_unknown_voltage_transformer_prevents_complete():
    s = sub()
    compute_capacity(s, [trafo(1, mva=40), trafo(2, primary=None, secondary=None)])
    assert s.mva_status == MVA_INFERRED_PARTIAL
    assert "transformers_with_unknown_voltage" in s.quality_flags


def test_multiple_devices_rating_is_only_a_lower_bound():
    s = sub()
    compute_capacity(s, [trafo(1, mva=40, flags=["rating_with_multiple_devices"])])
    assert s.mva_status == MVA_INFERRED_PARTIAL and s.mva_value == Decimal(40)


def test_own_rating_is_observed():
    s = sub(rating=120)
    compute_capacity(s, [trafo(1, mva=40)])
    assert s.mva_status == MVA_OBSERVED and s.mva_value == Decimal(120)
    assert "observed_differs_from_transformer_sum" in s.quality_flags


def test_unknown_reasons():
    s = sub()
    compute_capacity(s, [])
    assert (s.mva_status, s.mva_unknown_reason) == (MVA_UNKNOWN, "no_transformers_mapped")

    s = sub()
    compute_capacity(s, [trafo(1, status="rejected")])
    assert s.mva_unknown_reason == "transformer_rating_rejected"

    s = sub()
    compute_capacity(s, [trafo(1, primary=None, secondary=None)])
    assert s.mva_unknown_reason == "transformer_voltage_unknown"

    s = sub()
    s.geom_kind = "point"
    compute_capacity(s, [])
    assert s.mva_unknown_reason == "no_polygon_geometry"


def test_transformer_above_substation_voltage_is_flagged_and_used():
    s = sub(max_kv=110)
    compute_capacity(s, [trafo(1, 220, 110, mva=200)])
    assert "transformer_voltage_above_substation_voltage" in s.quality_flags
    assert s.mva_level_kv == 220 and s.mva_value == Decimal(200)
