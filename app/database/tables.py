from __future__ import annotations

from dataclasses import dataclass

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

SUBSTATIONS = "substations_mva"
TRANSFORMERS = "substation_transformers"
REJECTS = "ingest_rejects"
RUNS = "ingest_runs"


@dataclass
class StagingTables:
    metadata: MetaData
    substations: Table
    transformers: Table
    rejects: Table
    runs: Table


def build_tables(schema: str) -> StagingTables:
    md = MetaData(schema=schema)

    runs = Table(
        RUNS, md,
        Column("run_id", Text, primary_key=True),
        Column("status", Text, nullable=False),            # success | partial | failed
        Column("source_endpoint", Text),
        Column("bbox", Text),
        Column("region_iso", Text),
        Column("osm_snapshot_ts", DateTime(timezone=True)),
        Column("areas_snapshot_ts", DateTime(timezone=True)),
        Column("tiles_total", Integer),
        Column("tiles_ok", Integer),
        Column("tiles_failed", Integer),
        Column("raw_manifest_sha256", Text),
        Column("stats", JSONB),
        Column("loaded_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        Column("error", Text),
    )

    substations = Table(
        SUBSTATIONS, md,
        Column("osm_type", Text, nullable=False),
        Column("osm_id", BigInteger, nullable=False),
        Column("name", Text),
        Column("operators", ARRAY(Text)),
        Column("operator_wikidata", Text),
        Column("wikidata", Text),
        Column("substation_type", Text),
        Column("lifecycle", Text, nullable=False),
        Column("frequency_hz", Float),
        Column("voltage_raw", Text),
        Column("voltages_v", ARRAY(Integer)),
        Column("hv_levels_kv", ARRAY(Integer), nullable=False),
        Column("max_voltage_kv", Integer),
        Column("rating_raw", Text),
        Column("rating_status", Text, nullable=False),
        Column("rating_reject_reason", Text),
        Column("mva_status", Text, nullable=False),
        Column("mva_value", Numeric(12, 3)),
        Column("mva_level_kv", Integer),
        Column("mva_is_lower_bound", Boolean, nullable=False),
        Column("mva_unknown_reason", Text),
        Column("capacity_by_level", JSONB),
        Column("transformer_count", Integer, nullable=False),
        Column("transformer_rated_count", Integer, nullable=False),
        Column("geom", Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False)),
        Column("centroid", Geometry(geometry_type="POINT", srid=4326, spatial_index=False)),
        Column("geom_kind", Text),
        Column("geom_valid", Boolean, nullable=False),
        Column("area_m2", Float),
        Column("is_grid_candidate", Boolean, nullable=False),
        Column("non_grid_reason", Text),
        Column("is_duplicate", Boolean, nullable=False),
        Column("duplicate_of", Text),
        Column("site_id", Text),
        Column("site_group_reason", Text),
        Column("quality_flags", ARRAY(Text)),
        Column("tags", JSONB),
        Column("source_file", Text),
        Column("content_hash", Text, nullable=False),
        Column("osm_snapshot_ts", DateTime(timezone=True)),
        Column("first_seen_run_id", Text, nullable=False),
        Column("last_seen_run_id", Text, nullable=False),
        Column("present_in_latest_snapshot", Boolean, nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        PrimaryKeyConstraint("osm_type", "osm_id", name="pk_substations_mva"),
        Index("ix_substations_mva_geom", "geom", postgresql_using="gist"),
        Index("ix_substations_mva_centroid", "centroid", postgresql_using="gist"),
        Index("ix_substations_mva_site_id", "site_id"),
        Index("ix_substations_mva_mva_status", "mva_status"),
    )

    transformers = Table(
        TRANSFORMERS, md,
        Column("osm_type", Text, nullable=False),
        Column("osm_id", BigInteger, nullable=False),
        Column("substation_osm_type", Text),
        Column("substation_osm_id", BigInteger),
        Column("assignment_status", Text, nullable=False),
        Column("name", Text),
        Column("ref", Text),
        Column("transformer_type", Text),
        Column("frequency_hz", Float),
        Column("primary_v", Integer),
        Column("secondary_v", Integer),
        Column("tertiary_v", Integer),
        Column("rating_raw", Text),
        Column("rating_status", Text, nullable=False),
        Column("rating_mva", Numeric(12, 3)),
        Column("rating_reject_reason", Text),
        Column("geom", Geometry(geometry_type="POINT", srid=4326, spatial_index=False)),
        Column("quality_flags", ARRAY(Text)),
        Column("tags", JSONB),
        Column("source_file", Text),
        Column("content_hash", Text, nullable=False),
        Column("osm_snapshot_ts", DateTime(timezone=True)),
        Column("first_seen_run_id", Text, nullable=False),
        Column("last_seen_run_id", Text, nullable=False),
        Column("present_in_latest_snapshot", Boolean, nullable=False),
        Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        PrimaryKeyConstraint("osm_type", "osm_id", name="pk_substation_transformers"),
        Index("ix_substation_transformers_geom", "geom", postgresql_using="gist"),
        Index("ix_substation_transformers_substation", "substation_osm_type", "substation_osm_id"),
    )

    rejects = Table(
        REJECTS, md,
        Column("id", BigInteger, primary_key=True, autoincrement=True),
        Column("run_id", Text, nullable=False),
        Column("element_kind", Text, nullable=False),
        Column("osm_type", Text),
        Column("osm_id", BigInteger),
        Column("reason", Text, nullable=False),
        Column("detail", Text),
        Column("raw", JSONB),
        Column("source_file", Text),
        Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        UniqueConstraint("run_id", "element_kind", "osm_type", "osm_id", "reason",
                         name="uq_ingest_rejects_run_element_reason",
                         postgresql_nulls_not_distinct=True),
    )

    return StagingTables(md, substations, transformers, rejects, runs)
