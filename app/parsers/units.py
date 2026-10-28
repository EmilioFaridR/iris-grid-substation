from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# Voltage

_VOLTAGE_RE = re.compile(r"^\s*(\d+(?:[.,]\d{1,3})?)\s*(kv|v)?\s*$", re.IGNORECASE)
_UNITLESS_KV_VALUES = {110, 220, 380}


@dataclass(frozen=True)
class VoltageParse:
    volts: tuple[int, ...] = ()
    rejected_tokens: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def kv_levels(self) -> tuple[int, ...]:
        """Distinct voltage levels in whole kV, highest first."""
        return tuple(sorted({v // 1000 for v in self.volts if v >= 1000}, reverse=True))

    @property
    def max_kv(self) -> int | None:
        levels = self.kv_levels
        return levels[0] if levels else None


def clean_decimal(value: Decimal) -> Decimal:
    """Normalise without scientific notation: Decimal('800.000') -> 800, '31.50' -> 31.5."""
    if value == value.to_integral_value():
        return value.quantize(Decimal(1))
    return value.normalize()


def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None


def parse_voltage_token(token: str) -> tuple[int | None, str | None]:
    """Parse one voltage value. Returns (volts, warning_or_reject_reason)."""
    match = _VOLTAGE_RE.match(token or "")
    if not match:
        return None, "non_numeric"
    if "," in match.group(1) and len(match.group(1).split(",")[0]) >= 4:
        # "110000,200" is a list written with the wrong separator, not a decimal
        return None, "comma_separated_list"
    number = _to_decimal(match.group(1))
    if number is None or number <= 0:
        return None, "non_positive"
    unit = (match.group(2) or "").lower()

    if unit == "kv":
        return int(number * 1000), None
    if unit == "v":
        return int(number), None
    # No unit: OSM convention is volts ...
    if number >= 1000:
        return int(number), None
    # ... except the obvious transmission levels written in kV.
    if number == number.to_integral_value() and int(number) in _UNITLESS_KV_VALUES:
        return int(number) * 1000, "unitless_kv_assumed"
    return int(number), "unitless_low_value"


def parse_voltage(raw: str | None) -> VoltageParse:
    """Parse an OSM `voltage` / `voltage:*` value such as '220000;110000'."""
    if raw is None or not str(raw).strip():
        return VoltageParse(rejected_tokens=(), warnings=("missing",))

    volts: list[int] = []
    rejected: list[str] = []
    warnings: list[str] = []
    for token in str(raw).split(";"):
        token = token.strip()
        if not token:
            continue
        value, note = parse_voltage_token(token)
        if value is None:
            rejected.append(token)
            warnings.append(f"rejected_token:{note}")
            continue
        if note:
            warnings.append(note)
        if value not in volts:
            volts.append(value)
    return VoltageParse(tuple(volts), tuple(rejected), tuple(dict.fromkeys(warnings)))


def parse_single_voltage(raw: str | None) -> tuple[int | None, str | None]:
    """For voltage:primary/secondary/tertiary: exactly one value expected."""
    parsed = parse_voltage(raw)
    if raw is None or not str(raw).strip():
        return None, None
    if parsed.rejected_tokens:
        return None, f"unparseable:{raw}"
    if len(parsed.volts) != 1:
        return None, f"multi_value:{raw}"
    return parsed.volts[0], (parsed.warnings[0] if parsed.warnings else None)


# Rating (apparent power)

_RATING_RE = re.compile(
    r"^\s*(?:(\d+)\s*[x×*]\s*)?(\d+(?:[.,]\d+)?)\s*([gmk]?va|[gmk]?w)?\s*$",
    re.IGNORECASE,
)
_VA_FACTORS_TO_MVA = {
    "gva": Decimal(1000),
    "mva": Decimal(1),
    "kva": Decimal("0.001"),
    "va": Decimal("0.000001"),
}
RATING_PARSED = "parsed"
RATING_MISSING = "missing"
RATING_REJECTED = "rejected"
MAX_PLAUSIBLE_MVA = Decimal(5000)


@dataclass(frozen=True)
class RatingParse:
    status: str
    mva: Decimal | None = None
    reason: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def parse_rating(raw: str | None) -> RatingParse:
    """Parse an OSM `rating` value into MVA, or explain why it cannot be used."""
    if raw is None or not str(raw).strip():
        return RatingParse(RATING_MISSING)
    text = str(raw).strip()

    if ";" in text:
        return RatingParse(RATING_REJECTED, reason="multi_value_ambiguous")

    match = _RATING_RE.match(text)
    if not match:
        return RatingParse(RATING_REJECTED, reason="unparseable")

    multiplier_text, number_text, unit_text = match.groups()
    unit = (unit_text or "").lower()
    if not unit:
        return RatingParse(RATING_REJECTED, reason="missing_unit")
    if unit.endswith("w"):
        # Active power (W/kW/MW/GW) is not apparent power (VA): no conversion.
        return RatingParse(RATING_REJECTED, reason="active_power_unit_not_mva")

    number = _to_decimal(number_text)
    if number is None or number <= 0:
        return RatingParse(RATING_REJECTED, reason="non_positive")

    warnings: list[str] = []
    value = number * _VA_FACTORS_TO_MVA[unit]
    if multiplier_text:
        count = int(multiplier_text)
        if count <= 0:
            return RatingParse(RATING_REJECTED, reason="non_positive")
        value *= count
        warnings.append("multiplier_applied")
    if unit != "mva":
        warnings.append(f"converted_from_{unit}")
    if value > MAX_PLAUSIBLE_MVA:
        return RatingParse(RATING_REJECTED, reason="implausible_magnitude")

    return RatingParse(RATING_PARSED, mva=clean_decimal(value), warnings=tuple(warnings))


# Frequency


def parse_frequency(raw: str | None) -> float | None:
    """'50' -> 50.0, '16.7' -> 16.7, '50;16.7' -> highest value, else None."""
    if not raw:
        return None
    values: list[float] = []
    for token in str(raw).split(";"):
        try:
            values.append(float(token.strip().replace(",", ".")))
        except ValueError:
            continue
    return max(values) if values else None
