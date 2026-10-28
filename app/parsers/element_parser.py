
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.config import Settings
from app.models import Reject, SubstationRecord, TransformerRecord
from app.parsers.geometry import (
    geometry_from_element,
    in_bbox,
    representative_point,
    validate_polygon,
)
from app.parsers.units import (
    RATING_PARSED,
    parse_frequency,
    parse_rating,
    parse_single_voltage,
    parse_voltage,
)

_TYPE_ORDER = {"node": 0, "way": 1, "relation": 2}


@dataclass
class ParseResult:
    substations: dict[str, SubstationRecord] = field(default_factory=dict)
    transformers: dict[str, TransformerRecord] = field(default_factory=dict)
    rejects: list[Reject] = field(default_factory=list)
    stats: Counter = field(default_factory=Counter)
    _reject_keys: set[tuple[str, str]] = field(default_factory=set, repr=False)


def _split_multi(value: str | None) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    for part in value.split(";"):
        part = " ".join(part.split())
        if part and part not in out:
            out.append(part)
    return out


def _lifecycle(tags: dict) -> str:
    if tags.get("disused") == "yes" or "disused:power" in tags:
        return "disused"
    if tags.get("abandoned") == "yes" or "abandoned:power" in tags:
        return "abandoned"
    return "active"


# Substations


def parse_substation(element: dict, settings: Settings, source_file: str | None):
    tags = element.get("tags") or {}
    osm_type, osm_id = element["type"], int(element["id"])

    def reject(reason: str, detail: str = "") -> Reject:
        return Reject("substation", osm_type, osm_id, reason, detail, element, source_file)

    # ---- voltage ------------------------------------------------------
    voltage = parse_voltage(tags.get("voltage"))
    hv_levels = [kv for kv in voltage.kv_levels if kv in settings.target_voltages]
    if not hv_levels:
        return reject("no_target_voltage", f"voltage={tags.get('voltage')!r}")

    # ---- geometry -----------------------------------------------------
    geo = geometry_from_element(element)
    if not geo.ok:
        return reject(geo.reject_reason or "missing_geometry")

    rec = SubstationRecord(osm_type=osm_type, osm_id=osm_id, tags=tags, source_file=source_file)
    rec.name = tags.get("name")
    rec.operators = _split_multi(tags.get("operator"))
    rec.operator_wikidata = tags.get("operator:wikidata")
    rec.wikidata = tags.get("wikidata")
    rec.substation_type = tags.get("substation")
    rec.lifecycle = _lifecycle(tags)
    rec.frequency_hz = parse_frequency(tags.get("frequency"))

    rec.voltage_raw = tags.get("voltage")
    rec.voltages_v = list(voltage.volts)
    rec.hv_levels_kv = hv_levels
    rec.max_voltage_kv = voltage.max_kv
    for warning in voltage.warnings:
        rec.flag(f"voltage_{warning}")

    # ---- own rating tag ----------------------------------------------
    rating = parse_rating(tags.get("rating"))
    rec.rating_raw = tags.get("rating")
    rec.rating_status = rating.status
    rec.rating_mva = rating.mva
    rec.rating_reject_reason = rating.reason
    for warning in rating.warnings:
        rec.flag(f"rating_{warning}")

    # ---- geometry details ---------------------------------------------
    rec.geometry, rec.geom_kind = geo.geometry, geo.kind
    for issue in geo.issues:
        rec.flag(issue)
    if rec.geom_kind in ("polygon", "multipolygon"):
        rec.geom_valid, rec.area_m2, issues = validate_polygon(
            rec.geometry, settings.max_plausible_substation_area_m2
        )
        for issue in issues:
            rec.flag(issue)
    else:
        rec.geom_valid = rec.geom_kind == "point"
    rec.centroid = representative_point(rec.geometry)
    if not in_bbox(rec.centroid, settings.bbox_tuple):
        rec.flag("outside_configured_bbox")

    # ---- classification -----------------------------------------------
    if rec.lifecycle != "active":
        rec.is_grid_candidate, rec.non_grid_reason = False, f"lifecycle:{rec.lifecycle}"
    elif rec.substation_type in settings.non_grid_types:
        rec.is_grid_candidate, rec.non_grid_reason = False, f"type:{rec.substation_type}"

    # ---- descriptive completeness -------------------------------------
    if not rec.name:
        rec.flag("missing_name")
    if not rec.operators:
        rec.flag("missing_operator")
    if "fixme" in tags or "FIXME" in tags:
        rec.flag("has_fixme")
    return rec


# Transformers


def parse_transformer(element: dict, settings: Settings, source_file: str | None):
    tags = element.get("tags") or {}
    osm_type, osm_id = element["type"], int(element["id"])

    geo = geometry_from_element(element)
    if not geo.ok:
        return Reject("transformer", osm_type, osm_id, geo.reject_reason or "missing_geometry",
                      "", element, source_file)

    rec = TransformerRecord(osm_type=osm_type, osm_id=osm_id, tags=tags, source_file=source_file)
    rec.name = tags.get("name")
    rec.ref = tags.get("ref")
    rec.transformer_type = tags.get("transformer")
    rec.frequency_hz = parse_frequency(tags.get("frequency"))
    rec.point = representative_point(geo.geometry)

    # ---- voltages -----------------------------------------------------
    for attr, key in (("primary_v", "voltage:primary"), ("secondary_v", "voltage:secondary"),
                      ("tertiary_v", "voltage:tertiary")):
        value, note = parse_single_voltage(tags.get(key))
        setattr(rec, attr, value)
        if note:
            rec.flag(f"{key.replace(':', '_')}_{note.split(':')[0]}")
    if rec.primary_v is None and tags.get("voltage"):
        # Legacy tagging: voltage=110000;10000 -> highest value is the primary side.
        legacy = parse_voltage(tags.get("voltage"))
        if legacy.volts:
            ordered = sorted(legacy.volts, reverse=True)
            rec.primary_v = ordered[0]
            rec.secondary_v = rec.secondary_v or (ordered[1] if len(ordered) > 1 else None)
            rec.flag("voltage_from_legacy_tag")
    if rec.primary_v is None:
        rec.flag("missing_primary_voltage")

    # ---- rating -------------------------------------------------------
    rating = parse_rating(tags.get("rating"))
    rec.rating_raw = tags.get("rating")
    rec.rating_status = rating.status
    rec.rating_mva = rating.mva
    rec.rating_reject_reason = rating.reason
    for warning in rating.warnings:
        rec.flag(f"rating_{warning}")
    devices = tags.get("devices")
    if rating.status == RATING_PARSED and devices and devices.strip() not in ("1", ""):
        # Does the rating describe one device or all of them? OSM is ambiguous.
        rec.flag("rating_with_multiple_devices")
    return rec


# Batch


def element_sort_key(element: dict) -> tuple[int, int]:
    return _TYPE_ORDER.get(element.get("type"), 9), int(element.get("id", 0))


def parse_elements(
    batches: list[tuple[str | None, list[dict]]],
    settings: Settings,
    result: ParseResult | None = None,
) -> ParseResult:
    """Parse several Overpass responses (one per tile) into one ParseResult.

    `batches` is a list of (source_file, elements). Elements returned by more
    than one tile (tile overlap) are kept once.
    """
    result = result or ParseResult()
    stats = result.stats
    for source_file, elements in batches:
        for element in sorted(elements, key=element_sort_key):
            stats["elements_received"] += 1
            if "type" not in element or "id" not in element:
                stats["elements_malformed"] += 1
                result.rejects.append(Reject("element", None, None, "malformed_element",
                                             "missing type/id", element, source_file))
                continue

            key = f"{element['type']}/{element['id']}"
            if key in result.substations or key in result.transformers:
                stats["tile_overlap_duplicates"] += 1
                continue

            power = (element.get("tags") or {}).get("power")
            if power == "substation":
                parsed = parse_substation(element, settings, source_file)
                target = result.substations
            elif power == "transformer":
                parsed = parse_transformer(element, settings, source_file)
                target = result.transformers
            else:
                parsed = Reject("element", element["type"], int(element["id"]),
                                "unexpected_power_tag", f"power={power!r}", element, source_file)
                target = None

            if isinstance(parsed, Reject):
                if (parsed.key, parsed.reason) in result._reject_keys:
                    stats["tile_overlap_duplicates"] += 1
                    continue
                result._reject_keys.add((parsed.key, parsed.reason))
                result.rejects.append(parsed)
                stats[f"rejected_{parsed.element_kind}"] += 1
            else:
                target[key] = parsed
                stats[f"parsed_{'substation' if power == 'substation' else 'transformer'}"] += 1
    return result
