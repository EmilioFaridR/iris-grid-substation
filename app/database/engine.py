from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import Settings


class DatabaseNotConfigured(RuntimeError):
    pass


def make_engine(settings: Settings) -> Engine:
    if not settings.database_url:
        raise DatabaseNotConfigured(
            "IRIS_DATABASE_URL is not set. Copy .env.example to .env (docker compose sets it for you)."
        )
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)
