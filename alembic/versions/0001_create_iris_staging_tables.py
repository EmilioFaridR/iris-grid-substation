"""create iris_staging tables

Creates the four staging tables of Task 098 in the schema configured by
IRIS_STAGING_SCHEMA (default iris_staging; iris_core is refused by Settings).
Generated with `alembic revision --autogenerate` and reviewed.

Revision ID: 0001
Revises: 
Create Date: 2026-10-27 13:06:11.092375
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

from app.config import Settings

SCHEMA = Settings().staging_schema

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ingest_rejects',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.Text(), nullable=False),
    sa.Column('element_kind', sa.Text(), nullable=False),
    sa.Column('osm_type', sa.Text(), nullable=True),
    sa.Column('osm_id', sa.BigInteger(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('source_file', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'element_kind', 'osm_type', 'osm_id', 'reason', name='uq_ingest_rejects_run_element_reason', postgresql_nulls_not_distinct=True),
    schema=SCHEMA
    )
    op.create_table('ingest_runs',
    sa.Column('run_id', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('source_endpoint', sa.Text(), nullable=True),
    sa.Column('bbox', sa.Text(), nullable=True),
    sa.Column('region_iso', sa.Text(), nullable=True),
    sa.Column('osm_snapshot_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('areas_snapshot_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tiles_total', sa.Integer(), nullable=True),
    sa.Column('tiles_ok', sa.Integer(), nullable=True),
    sa.Column('tiles_failed', sa.Integer(), nullable=True),
    sa.Column('raw_manifest_sha256', sa.Text(), nullable=True),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('loaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('run_id'),
    schema=SCHEMA
    )
    op.create_geospatial_table('substation_transformers',
    sa.Column('osm_type', sa.Text(), nullable=False),
    sa.Column('osm_id', sa.BigInteger(), nullable=False),
    sa.Column('substation_osm_type', sa.Text(), nullable=True),
    sa.Column('substation_osm_id', sa.BigInteger(), nullable=True),
    sa.Column('assignment_status', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('ref', sa.Text(), nullable=True),
    sa.Column('transformer_type', sa.Text(), nullable=True),
    sa.Column('frequency_hz', sa.Float(), nullable=True),
    sa.Column('primary_v', sa.Integer(), nullable=True),
    sa.Column('secondary_v', sa.Integer(), nullable=True),
    sa.Column('tertiary_v', sa.Integer(), nullable=True),
    sa.Column('rating_raw', sa.Text(), nullable=True),
    sa.Column('rating_status', sa.Text(), nullable=False),
    sa.Column('rating_mva', sa.Numeric(precision=12, scale=3), nullable=True),
    sa.Column('rating_reject_reason', sa.Text(), nullable=True),
    sa.Column('geom', Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.Column('quality_flags', postgresql.ARRAY(sa.Text()), nullable=True),
    sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('source_file', sa.Text(), nullable=True),
    sa.Column('content_hash', sa.Text(), nullable=False),
    sa.Column('osm_snapshot_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_seen_run_id', sa.Text(), nullable=False),
    sa.Column('last_seen_run_id', sa.Text(), nullable=False),
    sa.Column('present_in_latest_snapshot', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('osm_type', 'osm_id', name='pk_substation_transformers'),
    schema=SCHEMA
    )
    op.create_geospatial_index('ix_substation_transformers_geom', 'substation_transformers', ['geom'], unique=False, schema=SCHEMA, postgresql_using='gist', postgresql_ops={})
    op.create_index('ix_substation_transformers_substation', 'substation_transformers', ['substation_osm_type', 'substation_osm_id'], unique=False, schema=SCHEMA)
    op.create_geospatial_table('substations_mva',
    sa.Column('osm_type', sa.Text(), nullable=False),
    sa.Column('osm_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('operators', postgresql.ARRAY(sa.Text()), nullable=True),
    sa.Column('operator_wikidata', sa.Text(), nullable=True),
    sa.Column('wikidata', sa.Text(), nullable=True),
    sa.Column('substation_type', sa.Text(), nullable=True),
    sa.Column('lifecycle', sa.Text(), nullable=False),
    sa.Column('frequency_hz', sa.Float(), nullable=True),
    sa.Column('voltage_raw', sa.Text(), nullable=True),
    sa.Column('voltages_v', postgresql.ARRAY(sa.Integer()), nullable=True),
    sa.Column('hv_levels_kv', postgresql.ARRAY(sa.Integer()), nullable=False),
    sa.Column('max_voltage_kv', sa.Integer(), nullable=True),
    sa.Column('rating_raw', sa.Text(), nullable=True),
    sa.Column('rating_status', sa.Text(), nullable=False),
    sa.Column('rating_reject_reason', sa.Text(), nullable=True),
    sa.Column('mva_status', sa.Text(), nullable=False),
    sa.Column('mva_value', sa.Numeric(precision=12, scale=3), nullable=True),
    sa.Column('mva_level_kv', sa.Integer(), nullable=True),
    sa.Column('mva_is_lower_bound', sa.Boolean(), nullable=False),
    sa.Column('mva_unknown_reason', sa.Text(), nullable=True),
    sa.Column('capacity_by_level', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('transformer_count', sa.Integer(), nullable=False),
    sa.Column('transformer_rated_count', sa.Integer(), nullable=False),
    sa.Column('geom', Geometry(srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.Column('centroid', Geometry(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeomFromEWKT', name='geometry'), nullable=True),
    sa.Column('geom_kind', sa.Text(), nullable=True),
    sa.Column('geom_valid', sa.Boolean(), nullable=False),
    sa.Column('area_m2', sa.Float(), nullable=True),
    sa.Column('is_grid_candidate', sa.Boolean(), nullable=False),
    sa.Column('non_grid_reason', sa.Text(), nullable=True),
    sa.Column('is_duplicate', sa.Boolean(), nullable=False),
    sa.Column('duplicate_of', sa.Text(), nullable=True),
    sa.Column('site_id', sa.Text(), nullable=True),
    sa.Column('site_group_reason', sa.Text(), nullable=True),
    sa.Column('quality_flags', postgresql.ARRAY(sa.Text()), nullable=True),
    sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('source_file', sa.Text(), nullable=True),
    sa.Column('content_hash', sa.Text(), nullable=False),
    sa.Column('osm_snapshot_ts', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_seen_run_id', sa.Text(), nullable=False),
    sa.Column('last_seen_run_id', sa.Text(), nullable=False),
    sa.Column('present_in_latest_snapshot', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('osm_type', 'osm_id', name='pk_substations_mva'),
    schema=SCHEMA
    )
    op.create_geospatial_index('ix_substations_mva_centroid', 'substations_mva', ['centroid'], unique=False, schema=SCHEMA, postgresql_using='gist', postgresql_ops={})
    op.create_geospatial_index('ix_substations_mva_geom', 'substations_mva', ['geom'], unique=False, schema=SCHEMA, postgresql_using='gist', postgresql_ops={})
    op.create_index('ix_substations_mva_mva_status', 'substations_mva', ['mva_status'], unique=False, schema=SCHEMA)
    op.create_index('ix_substations_mva_site_id', 'substations_mva', ['site_id'], unique=False, schema=SCHEMA)


def downgrade() -> None:
    op.drop_index('ix_substations_mva_site_id', table_name='substations_mva', schema=SCHEMA)
    op.drop_index('ix_substations_mva_mva_status', table_name='substations_mva', schema=SCHEMA)
    op.drop_geospatial_index('ix_substations_mva_geom', table_name='substations_mva', schema=SCHEMA, postgresql_using='gist', column_name='geom')
    op.drop_geospatial_index('ix_substations_mva_centroid', table_name='substations_mva', schema=SCHEMA, postgresql_using='gist', column_name='centroid')
    op.drop_geospatial_table('substations_mva', schema=SCHEMA)
    op.drop_index('ix_substation_transformers_substation', table_name='substation_transformers', schema=SCHEMA)
    op.drop_geospatial_index('ix_substation_transformers_geom', table_name='substation_transformers', schema=SCHEMA, postgresql_using='gist', column_name='geom')
    op.drop_geospatial_table('substation_transformers', schema=SCHEMA)
    op.drop_table('ingest_runs', schema=SCHEMA)
    op.drop_table('ingest_rejects', schema=SCHEMA)
