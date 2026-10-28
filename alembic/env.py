from __future__ import annotations

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import create_engine, text

from app.config import Settings
from app.database.tables import build_tables

settings = Settings()
if not settings.database_url:
    raise RuntimeError("IRIS_DATABASE_URL is not set")

SCHEMA = settings.staging_schema
target_metadata = build_tables(SCHEMA).metadata


def include_object(obj, name, type_, reflected, compare_to):
    # Only ever look at our own schema (never at iris_core, public, tiger, ...).
    if type_ == "table":
        return getattr(obj, "schema", None) == SCHEMA
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


def run_migrations_online() -> None:
    engine = create_engine(settings.database_url, future=True)
    with engine.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_object=include_object,
            process_revision_directives=alembic_helpers.writer,
            render_item=alembic_helpers.render_item,
        )
        with context.begin_transaction():
            context.execute(f'SET search_path TO "{SCHEMA}", public')
            context.run_migrations()


run_migrations_online()
