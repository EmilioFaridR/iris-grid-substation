from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from shapely.geometry.base import BaseGeometry

# mva_status values (README "Capacity semantics")
MVA_OBSERVED = "observed"                  # rating tag on the substation itself
MVA_INFERRED_COMPLETE = "inferred_complete"  # every transformer of the top level is rated
MVA_INFERRED_PARTIAL = "inferred_partial"    # only some are rated -> value is a lower bound
MVA_UNKNOWN = "unknown"

MVA_STATUSES = (MVA_OBSERVED, MVA_INFERRED_COMPLETE, MVA_INFERRED_PARTIAL, MVA_UNKNOWN)


@dataclass
class Reject:
    """An input element that could not be turned into a staging row."""

    element_kind: str            # substation | transformer | element
    osm_type: str | None
    osm_id: int | None
    reason: str
    detail: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    source_file: str | None = None

    @property
    def key(self) -> str:
        return f"{self.osm_type}/{self.osm_id}"


@dataclass
class SubstationRecord:
    osm_type: str
    osm_id: int
    tags: dict[str, str]
    source_file: str | None

    # identity / descriptive
    name: str | None = None
    operators: list[str] = field(default_factory=list)
    operator_wikidata: str | None = None
    wikidata: str | None = None
    substation_type: str | None = None
    lifecycle: str = "active"
    frequency_hz: float | None = None

    # voltage
    voltage_raw: str | None = None
    voltages_v: list[int] = field(default_factory=list)
    hv_levels_kv: list[int] = field(default_factory=list)
    max_voltage_kv: int | None = None

    # own rating tag
    rating_raw: str | None = None
    rating_status: str = "missing"
    rating_mva: Decimal | None = None
    rating_reject_reason: str | None = None

    # geometry (WGS84 / EPSG:4326)
    geometry: BaseGeometry | None = None
    geom_kind: str | None = None          # polygon | multipolygon | point | center_only | linestring
    geom_valid: bool = False
    area_m2: float | None = None
    centroid: BaseGeometry | None = None

    # classification
    is_grid_candidate: bool = True
    non_grid_reason: str | None = None
    is_duplicate: bool = False
    duplicate_of: str | None = None
    site_id: str | None = None
    site_group_reason: str | None = None

    # capacity
    mva_status: str = MVA_UNKNOWN
    mva_value: Decimal | None = None
    mva_level_kv: int | None = None
    mva_is_lower_bound: bool = False
    mva_unknown_reason: str | None = None
    capacity_by_level: list[dict[str, Any]] = field(default_factory=list)
    transformer_count: int = 0
    transformer_rated_count: int = 0

    quality_flags: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.osm_type}/{self.osm_id}"

    @property
    def has_polygon(self) -> bool:
        return self.geom_kind in ("polygon", "multipolygon") and self.geometry is not None

    def flag(self, value: str) -> None:
        if value not in self.quality_flags:
            self.quality_flags.append(value)


@dataclass
class TransformerRecord:
    osm_type: str
    osm_id: int
    tags: dict[str, str]
    source_file: str | None

    name: str | None = None
    ref: str | None = None
    transformer_type: str | None = None
    frequency_hz: float | None = None
    primary_v: int | None = None
    secondary_v: int | None = None
    tertiary_v: int | None = None

    rating_raw: str | None = None
    rating_status: str = "missing"
    rating_mva: Decimal | None = None
    rating_reject_reason: str | None = None

    point: BaseGeometry | None = None
    substation_key: str | None = None
    assignment_status: str = "unassigned"

    quality_flags: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.osm_type}/{self.osm_id}"

    @property
    def primary_kv(self) -> int | None:
        return None if self.primary_v is None else self.primary_v // 1000

    def flag(self, value: str) -> None:
        if value not in self.quality_flags:
            self.quality_flags.append(value)
