from decimal import Decimal

import pytest

from app.parsers.units import (
    RATING_MISSING,
    RATING_PARSED,
    RATING_REJECTED,
    clean_decimal,
    parse_frequency,
    parse_rating,
    parse_single_voltage,
    parse_voltage,
)

# voltage



@pytest.mark.parametrize(
    "raw, volts, kv_levels",
    [
        ("110000", (110000,), (110,)),
        ("220000;110000", (220000, 110000), (220, 110)),   # real: Station Wambel
        ("110000;15000", (110000, 15000), (110, 15)),       # real: DB traction
        ("380000", (380000,), (380,)),
        ("110 kV", (110000,), (110,)),
        ("110kV", (110000,), (110,)),
        ("110KV", (110000,), (110,)),
        ("110000 V", (110000,), (110,)),
        ("0.4 kV", (400,), ()),
        (" 380000 ; 220000 ;110000 ", (380000, 220000, 110000), (380, 220, 110)),
        ("110000;110000", (110000,), (110,)),
    ],
)
def test_parse_voltage_valid(raw, volts, kv_levels):
    parsed = parse_voltage(raw)
    assert parsed.volts == volts
    assert parsed.kv_levels == kv_levels
    assert parsed.rejected_tokens == ()


def test_unitless_transmission_value_is_assumed_kv_and_flagged():
    parsed = parse_voltage("380")
    assert parsed.volts == (380000,)
    assert "unitless_kv_assumed" in parsed.warnings


def test_unitless_small_value_follows_osm_convention_volts():
    parsed = parse_voltage("400")
    assert parsed.volts == (400,)
    assert "unitless_low_value" in parsed.warnings


@pytest.mark.parametrize("raw", ["medium", "high", "abc", "110000,20000", "110000,200", "-110000"])
def test_parse_voltage_rejects_non_numeric_tokens(raw):
    parsed = parse_voltage(raw)
    assert parsed.volts == ()
    assert parsed.rejected_tokens == (raw.strip(),)


def test_parse_voltage_keeps_good_tokens_when_one_is_bad():
    parsed = parse_voltage("110000;medium")
    assert parsed.volts == (110000,)
    assert parsed.rejected_tokens == ("medium",)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_parse_voltage_missing(raw):
    parsed = parse_voltage(raw)
    assert parsed.volts == () and parsed.max_kv is None


def test_parse_single_voltage():
    assert parse_single_voltage("110000") == (110000, None)
    assert parse_single_voltage(None) == (None, None)
    value, note = parse_single_voltage("medium")       # real: 3 transformers in Dortmund
    assert value is None and note.startswith("unparseable")
    value, note = parse_single_voltage("110000;20000")
    assert value is None and note.startswith("multi_value")


# rating


@pytest.mark.parametrize(
    "raw, mva",
    [
        ("40 MVA", Decimal(40)),       # real: Station Wambel transformers
        ("40MVA", Decimal(40)),
        ("40 mva", Decimal(40)),
        ("31.5 MVA", Decimal("31.5")),
        ("31,5 MVA", Decimal("31.5")),  # German decimal comma
        ("40000 kVA", Decimal(40)),
        ("630 kVA", Decimal("0.63")),
        ("1 GVA", Decimal(1000)),
        ("2x40 MVA", Decimal(80)),
        ("2 x 40 MVA", Decimal(80)),
        ("2×40 MVA", Decimal(80)),
    ],
)
def test_parse_rating_valid(raw, mva):
    parsed = parse_rating(raw)
    assert parsed.status == RATING_PARSED
    assert parsed.mva == mva


@pytest.mark.parametrize(
    "raw, reason",
    [
        ("10 MW", "active_power_unit_not_mva"),     # real: DB Energie Hagen transformers
        ("500 kW", "active_power_unit_not_mva"),
        ("40", "missing_unit"),
        ("40 MVA;40 MVA", "multi_value_ambiguous"),
        ("40;40", "multi_value_ambiguous"),
        ("about 40 MVA", "unparseable"),
        ("0 MVA", "non_positive"),
        ("90000 MVA", "implausible_magnitude"),
    ],
)
def test_parse_rating_rejected_with_reason(raw, reason):
    parsed = parse_rating(raw)
    assert parsed.status == RATING_REJECTED
    assert parsed.mva is None
    assert parsed.reason == reason


def test_parse_rating_missing():
    assert parse_rating(None).status == RATING_MISSING
    assert parse_rating(" ").status == RATING_MISSING


def test_rating_conversion_and_multiplier_are_flagged():
    assert "converted_from_kva" in parse_rating("40000 kVA").warnings
    assert "multiplier_applied" in parse_rating("2x40 MVA").warnings


def test_clean_decimal_has_no_scientific_notation():
    assert str(clean_decimal(Decimal("800.000"))) == "800"
    assert str(clean_decimal(Decimal("31.50"))) == "31.5"


def test_parse_frequency():
    assert parse_frequency("50") == 50.0
    assert parse_frequency("16.7") == 16.7
    assert parse_frequency("x") is None
