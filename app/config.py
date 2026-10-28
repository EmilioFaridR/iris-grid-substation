from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Bounding box used by the IRIS core pipeline for North Rhine-Westphalia
# (see iris-sample/test_overpass_query.txt): south, west, north, east.
NRW_BBOX = (50.32, 5.86, 52.53, 9.46)

# Schemas this task must never write to.
FORBIDDEN_SCHEMAS = {"iris_core", "public"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IRIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


    # Source: Overpass API
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_status_url: str = "https://overpass-api.de/api/status"
    # Only public endpoints whose usage policy allows this kind of query.
    allowed_overpass_hosts: str = "overpass-api.de,lz4.overpass-api.de,z.overpass-api.de"
    user_agent: str = "iris-grid-substation/0.1 (IRIS candidate assignment; contact via repository)"

    request_timeout_s: float = 240.0
    query_timeout_s: int = 180
    min_request_interval_s: float = 5.0
    max_retries: int = 4
    backoff_base_s: float = 10.0
    backoff_max_s: float = 300.0
    check_status_endpoint: bool = True


    # Scope
    bbox: str = ",".join(str(v) for v in NRW_BBOX)
    tile_deg: float = 1.0
    # ISO 3166-2 code used to clip the bbox to the administrative region.
    # Empty string disables the region filter (bbox only).
    region_iso: str = "DE-NW"
    target_voltages_kv: str = "110,220,380"


    # Business rules (documented in README "Assumptions")
    non_grid_substation_types: str = "traction,transition"
    site_name_max_distance_m: float = 500.0
    site_adjacency_max_distance_m: float = 30.0
    max_plausible_substation_area_m2: float = 2_000_000.0

    # Storage
    database_url: str | None = None
    staging_schema: str = "iris_staging"
    data_dir: Path = Path("data")
    report_dir: Path = Path("reports")


    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    # Validation
    @field_validator("staging_schema")
    @classmethod
    def _staging_only(cls, value: str) -> str:
        if value.lower() in FORBIDDEN_SCHEMAS or not value.lower().startswith("iris_staging"):
            raise ValueError(
                f"Refusing schema '{value}': this task may only write to an "
                "'iris_staging*' schema (never iris_core or public)."
            )
        return value

    @model_validator(mode="after")
    def _authorized_endpoint(self) -> "Settings":
        allowed = {h.strip().lower() for h in self.allowed_overpass_hosts.split(",") if h.strip()}
        for url in (self.overpass_url, self.overpass_status_url):
            host = (urlparse(url).hostname or "").lower()
            if host not in allowed:
                raise ValueError(
                    f"Overpass host '{host}' is not in IRIS_ALLOWED_OVERPASS_HOSTS {sorted(allowed)}"
                )
        return self

    # Parsed helpers
    @property
    def bbox_tuple(self) -> tuple[float, float, float, float]:
        parts = [float(p) for p in self.bbox.split(",")]
        if len(parts) != 4:
            raise ValueError("IRIS_BBOX must be 'south,west,north,east'")
        south, west, north, east = parts
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise ValueError(f"IRIS_BBOX out of range or inverted: {self.bbox}")
        return south, west, north, east

    @property
    def target_voltages(self) -> frozenset[int]:
        return frozenset(int(v) for v in self.target_voltages_kv.split(",") if v.strip())

    @property
    def non_grid_types(self) -> frozenset[str]:
        return frozenset(t.strip() for t in self.non_grid_substation_types.split(",") if t.strip())

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw" / "overpass"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Small alias so tests can build isolated settings objects.
def make_settings(**overrides) -> Settings:
    return Settings(**overrides)


__all__ = ["Settings", "get_settings", "make_settings", "NRW_BBOX", "FORBIDDEN_SCHEMAS"]
