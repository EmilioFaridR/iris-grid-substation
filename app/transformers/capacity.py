from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from app.models import (
    MVA_INFERRED_COMPLETE,
    MVA_INFERRED_PARTIAL,
    MVA_OBSERVED,
    MVA_UNKNOWN,
    SubstationRecord,
    TransformerRecord,
)
from app.parsers.units import RATING_PARSED, RATING_REJECTED, clean_decimal


def _kv(volts: int | None) -> int | None:
    return None if volts is None else volts // 1000


def _level_summary(primary_kv, secondary_kv, trafos: list[TransformerRecord]) -> dict:
    rated = [t for t in trafos if t.rating_status == RATING_PARSED]
    total = sum((t.rating_mva for t in rated), Decimal(0))
    return {
        "primary_kv": primary_kv,
        "secondary_kv": secondary_kv,
        "transformer_count": len(trafos),
        "rated_count": len(rated),
        "rated_mva_sum": str(clean_decimal(total)) if rated else None,
        "complete": bool(trafos) and len(rated) == len(trafos),
        "transformers": sorted(t.key for t in trafos),
    }


def compute_capacity(sub: SubstationRecord, trafos: list[TransformerRecord]) -> None:
    trafos = sorted(trafos, key=lambda t: (t.osm_type, t.osm_id))
    sub.transformer_count = len(trafos)
    sub.transformer_rated_count = sum(t.rating_status == RATING_PARSED for t in trafos)

    # ---- breakdown by transformation level -----------------------------
    by_level: dict[tuple, list[TransformerRecord]] = defaultdict(list)
    for t in trafos:
        by_level[(_kv(t.primary_v), _kv(t.secondary_v))].append(t)
    sub.capacity_by_level = [
        _level_summary(p, s, items)
        for (p, s), items in sorted(by_level.items(),
                                    key=lambda kv: (kv[0][0] is None, -(kv[0][0] or 0), -(kv[0][1] or 0)))
    ]

    known_primaries = [t.primary_kv for t in trafos if t.primary_kv is not None]
    top_kv = max([sub.max_voltage_kv or 0, *known_primaries]) or None
    if known_primaries and max(known_primaries) > (sub.max_voltage_kv or 0):
        sub.flag("transformer_voltage_above_substation_voltage")
    top = [t for t in trafos if t.primary_kv == top_kv]
    unclassified = [t for t in trafos if t.primary_kv is None]
    top_rated = [t for t in top if t.rating_status == RATING_PARSED]
    inferred_sum = sum((t.rating_mva for t in top_rated), Decimal(0)) if top_rated else None

    top_keys = {t.key for t in top}
    lower_rated = [t for t in trafos if t.rating_status == RATING_PARSED and t.key not in top_keys]
    if lower_rated and not top_rated:
        sub.flag("rated_capacity_only_at_lower_level")

    # ---- observed --------------------------------------------------------
    if sub.rating_status == RATING_PARSED:
        sub.mva_status, sub.mva_value, sub.mva_level_kv = MVA_OBSERVED, sub.rating_mva, top_kv
        sub.mva_is_lower_bound, sub.mva_unknown_reason = False, None
        if inferred_sum is not None and inferred_sum != sub.rating_mva:
            sub.flag("observed_differs_from_transformer_sum")
        return

    sub.mva_level_kv = top_kv
    if sub.rating_status == RATING_REJECTED:
        sub.flag(f"own_rating_rejected:{sub.rating_reject_reason}")

    # ---- inferred --------------------------------------------------------
    if top_rated:
        complete = (
            len(top_rated) == len(top)
            and not unclassified
            and not any("rating_with_multiple_devices" in t.quality_flags for t in top_rated)
        )
        sub.mva_value = clean_decimal(inferred_sum)
        sub.mva_unknown_reason = None
        if complete:
            sub.mva_status, sub.mva_is_lower_bound = MVA_INFERRED_COMPLETE, False
        else:
            sub.mva_status, sub.mva_is_lower_bound = MVA_INFERRED_PARTIAL, True
            if len(top_rated) < len(top):
                sub.flag("unrated_top_level_transformers")
            if unclassified:
                sub.flag("transformers_with_unknown_voltage")
        return

    # ---- unknown ---------------------------------------------------------
    sub.mva_status, sub.mva_value, sub.mva_is_lower_bound = MVA_UNKNOWN, None, False
    if sub.is_duplicate:
        reason = "duplicate_of_other_substation"
    elif not sub.has_polygon:
        reason = "no_polygon_geometry"
    elif not trafos:
        reason = "no_transformers_mapped"
    elif not top:
        reason = "transformer_voltage_unknown" if unclassified else "no_top_level_transformers"
    elif any(t.rating_status == RATING_REJECTED for t in top):
        reason = "transformer_rating_rejected"
    else:
        reason = "transformers_unrated"
    sub.mva_unknown_reason = reason


def compute_all(substations: list[SubstationRecord], transformers: list[TransformerRecord]) -> Counter:
    by_sub: dict[str, list[TransformerRecord]] = defaultdict(list)
    for t in transformers:
        if t.substation_key:
            by_sub[t.substation_key].append(t)
    stats: Counter = Counter()
    for sub in substations:
        compute_capacity(sub, by_sub.get(sub.key, []))
        stats[sub.mva_status] += 1
    return stats
