from __future__ import annotations

import re
from dataclasses import dataclass

_REGION_RE = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")


@dataclass(frozen=True)
class Tile:
    row: int
    col: int
    south: float
    west: float
    north: float
    east: float

    @property
    def tile_id(self) -> str:
        return f"r{self.row:02d}_c{self.col:02d}"

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self.south, self.west, self.north, self.east


def make_tiles(bbox: tuple[float, float, float, float], tile_deg: float) -> list[Tile]:
    """Split a bbox into a deterministic grid of tiles (row-major, south-west first)."""
    if tile_deg <= 0:
        raise ValueError("tile_deg must be > 0")
    south, west, north, east = bbox
    tiles: list[Tile] = []
    row, lat = 0, south
    while lat < north - 1e-9:
        top = min(round(lat + tile_deg, 6), north)
        col, lon = 0, west
        while lon < east - 1e-9:
            right = min(round(lon + tile_deg, 6), east)
            tiles.append(Tile(row, col, round(lat, 6), round(lon, 6), top, right))
            col, lon = col + 1, right
        row, lat = row + 1, top
    return tiles


def voltage_regex(target_kv: frozenset[int] | set[int]) -> str:
    return "|".join(str(v) for v in sorted(target_kv))


def build_tile_query(
    tile_bbox: tuple[float, float, float, float],
    target_kv: frozenset[int] | set[int],
    query_timeout_s: int = 180,
    region_iso: str | None = None,
) -> str:
    south, west, north, east = tile_bbox
    bbox = f"({south},{west},{north},{east})"
    lines = [f"[out:json][timeout:{int(query_timeout_s)}];"]
    if region_iso:
        if not _REGION_RE.match(region_iso):
            raise ValueError(f"Invalid ISO 3166-2 region code: {region_iso!r}")
        lines.append(f'area["ISO3166-2"="{region_iso}"]["boundary"="administrative"]->.region;')
        spatial = f"(area.region){bbox}"
    else:
        spatial = bbox
    lines += [
        f'nwr["power"="substation"]["voltage"~"{voltage_regex(target_kv)}"]{spatial}->.subs;',
        ".subs out geom;",
        ".subs map_to_area->.subareas;",
        'nwr["power"="transformer"](area.subareas)->.trafos;',
        ".trafos out center;",
    ]
    return "\n".join(lines)
