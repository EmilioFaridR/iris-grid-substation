from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
DLAT, DLON = 0.00045, 0.0007  # ~50 m x ~49 m half-size at 51 N
OSM3S = {
    "timestamp_osm_base": "2026-10-27T18:00:00Z",
    "timestamp_areas_base": "2026-10-27T17:30:00Z",
    "copyright": "SYNTHETIC test data modelled on OpenStreetMap tagging (ODbL schema).",
}


def ring(lat, lon, dlat=DLAT, dlon=DLON):
    pts = [(lat - dlat, lon - dlon), (lat - dlat, lon + dlon), (lat + dlat, lon + dlon),
           (lat + dlat, lon - dlon), (lat - dlat, lon - dlon)]
    return [{"lat": round(a, 7), "lon": round(o, 7)} for a, o in pts]


def way(osm_id, geometry, **tags):
    return {"type": "way", "id": osm_id, "nodes": list(range(1, len(geometry) + 1)),
            "geometry": geometry, "tags": {"power": "substation", **tags}}


def node(osm_id, lat, lon, **tags):
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lon, "tags": tags}


def trafo(osm_id, lat, lon, **tags):
    return node(osm_id, lat, lon, power="transformer", **tags)


def build():
    els = []
    # A - Wambel pattern: 220/110 kV yard, only the 110/10 kV transformers are rated
    els.append(way(900001, ring(51.000, 7.000), name="Station Alpha", operator="Amprion",
                   substation="transmission", voltage="220000;110000", frequency="50"))
    for i, dl in enumerate((-0.0003, 0.0, 0.0003), start=1):
        els.append(trafo(910000 + i, 51.0001, 7.000 + dl, rating="40 MVA",
                         **{"voltage:primary": "110000", "voltage:secondary": "10000"}))
    for i, dl in enumerate((-0.0002, 0.0002), start=4):
        els.append(trafo(910000 + i, 50.9997, 7.000 + dl, transformer="main",
                         **{"voltage:primary": "220000", "voltage:secondary": "110000"}))

    # B - fully rated 110 kV substation + the same substation mapped again as a node
    els.append(way(900002, ring(51.010, 7.000), name="Station Beta", operator="Westnetz",
                   voltage="110000"))
    els.append(trafo(910011, 51.0101, 6.9998, rating="40 MVA",
                     **{"voltage:primary": "110000", "voltage:secondary": "20000"}))
    els.append(trafo(910012, 51.0101, 7.0002, rating="40MVA",
                     **{"voltage:primary": "110000", "voltage:secondary": "20000"}))
    els.append(node(900101, 51.0100, 7.0000, power="substation", name="Beta", voltage="110000"))

    # C - partially rated (German decimal comma, one unrated, one without voltage)
    els.append(way(900003, ring(51.020, 7.000), name="Station Gamma", operator="DONetz",
                   voltage="110000"))
    els.append(trafo(910021, 51.0201, 6.9998, rating="31,5 MVA",
                     **{"voltage:primary": "110000", "voltage:secondary": "10000"}))
    els.append(trafo(910022, 51.0201, 7.0002, **{"voltage:primary": "110000", "voltage:secondary": "10000"}))
    els.append(trafo(910023, 51.0198, 7.0000))

    # D - railway traction substation rated in MW (active power) + adjacent public substation
    els.append(way(900004, ring(51.030, 7.000), name="Unterwerk Delta", operator="DB Energie",
                   substation="traction", voltage="110000;15000", frequency="16.7"))
    for i, dl in enumerate((-0.0002, 0.0002), start=1):
        els.append(trafo(910030 + i, 51.0301, 7.000 + dl, rating="10 MW", transformer="traction",
                         frequency="16.7", **{"voltage:primary": "110000", "voltage:secondary": "15000"}))
    els.append(way(900014, ring(51.030, 7.0015), name="Station Omikron", operator="Westnetz",
                   voltage="110000", frequency="50"))

    # E - own rating on the substation (observed) that differs from its transformer
    els.append(way(900005, ring(51.040, 7.000), name="Station Epsilon", operator="Amprion",
                   voltage="380000", rating="300 MVA"))
    els.append(trafo(910041, 51.0401, 7.0000, rating="250 MVA",
                     **{"voltage:primary": "380000", "voltage:secondary": "110000"}))

    # F - Kruckel pattern: same name, one area per voltage level, ~14 m apart
    els.append(way(900006, ring(51.050, 7.000), name="Station Zeta", operator="Westnetz", voltage="110000"))
    els.append(way(900007, ring(51.050, 7.0016), name="Station Zeta", operator="Amprion", voltage="380000"))
    els.append(trafo(910051, 51.0501, 7.0016, rating="500000 kVA",
                     **{"voltage:primary": "380000", "voltage:secondary": "110000"}))
    els.append(trafo(910052, 51.0501, 7.0000, **{"voltage:primary": "110000", "voltage:secondary": "30000"}))

    # G - unclosed way (mapping error)
    open_ring = ring(51.060, 7.000)[:-1]
    els.append(way(900008, open_ring, name="Station Eta", voltage="110000"))

    # H - self-intersecting ("bow-tie") polygon with a rated transformer inside
    lat, lon = 51.070, 7.000
    bowtie = [(lat - DLAT, lon - DLON), (lat + DLAT, lon + DLON), (lat - DLAT, lon + DLON),
              (lat + DLAT, lon - DLON), (lat - DLAT, lon - DLON)]
    els.append(way(900009, [{"lat": a, "lon": o} for a, o in bowtie], name="Station Theta", voltage="110000"))
    els.append(trafo(910061, 51.0700, 7.0005, rating="63 MVA",
                     **{"voltage:primary": "110000", "voltage:secondary": "10000"}))

    # I - multipolygon relation (outer ring split in two open ways + inner hole)
    lat, lon = 51.080, 7.000
    sw, se = {"lat": lat - DLAT, "lon": lon - DLON}, {"lat": lat - DLAT, "lon": lon + DLON}
    ne, nw = {"lat": lat + DLAT, "lon": lon + DLON}, {"lat": lat + DLAT, "lon": lon - DLON}
    hole = ring(lat, lon, DLAT / 4, DLON / 4)
    els.append({
        "type": "relation", "id": 900501,
        "members": [
            {"type": "way", "ref": 1, "role": "outer", "geometry": [sw, se, ne]},
            {"type": "way", "ref": 2, "role": "outer", "geometry": [ne, nw, sw]},
            {"type": "way", "ref": 3, "role": "inner", "geometry": hole},
        ],
        "tags": {"type": "multipolygon", "power": "substation", "name": "Station Iota",
                 "operator": "Amprion", "voltage": "380000", "wikidata": "Q999001"},
    })
    els.append(trafo(910071, lat + 0.0003, lon - 0.0005, rating="2x400 MVA",
                     **{"voltage:primary": "380000", "voltage:secondary": "220000"}))
    # same site by wikidata, ~300 m away
    els.append(way(900010, ring(51.0827, 7.000), name="Iota Nord", voltage="220000", wikidata="Q999001"))

    # J - out-of-scope voltage that matches the loose Overpass regex ("110" in "11000")
    els.append(way(900011, ring(51.090, 7.000), name="Station Kappa", voltage="11000"))

    # K - unit variants
    els.append(way(900012, ring(51.100, 7.000), name="Station Lambda", voltage="110 kV"))
    els.append(trafo(910082, 51.1001, 7.0000, **{"voltage:primary": "110000", "voltage:secondary": "medium"}))
    els.append(node(900102, 51.110, 7.000, power="substation", name="Station My", voltage="380"))

    # L - corrupt coordinates
    els.append(node(900103, 95.0, 7.000, power="substation", name="Broken", voltage="110000"))

    # M - disused
    els.append(way(900013, ring(51.120, 7.000), name="Station Ny", voltage="110000", disused="yes"))

    # N - transformer outside any substation, element with unexpected power tag, malformed element
    els.append(trafo(910081, 51.200, 7.000, **{"voltage:primary": "110000"}))
    els.append({"type": "way", "id": 900900, "geometry": ring(51.13, 7.0), "tags": {"power": "line", "voltage": "110000"}})
    els.append({"tags": {"power": "substation"}})

    tile_a = {"version": 0.6, "generator": "synthetic", "osm3s": OSM3S, "elements": els}

    # Tile B overlaps tile A: Beta and its transformers are returned again.
    overlap_ids = {900002, 910011, 910012}
    tile_b = {"version": 0.6, "generator": "synthetic", "osm3s": OSM3S,
              "elements": [e for e in els if e.get("id") in overlap_ids]}

    tiles = []
    for name, payload in (("tile_a.json", tile_a), ("tile_b.json", tile_b)):
        data = json.dumps(payload, indent=1).encode("utf-8")
        (HERE / name).write_bytes(data)
        tiles.append({
            "tile_id": name[:-5], "status": "ok", "file": name,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "element_count": len(payload["elements"]),
            "timestamp_osm_base": OSM3S["timestamp_osm_base"],
            "timestamp_areas_base": OSM3S["timestamp_areas_base"],
        })
    manifest = {
        "run_id": "synthetic-fixture",
        "source": "SYNTHETIC fixture (tests/fixtures/synthetic/build_fixture.py)",
        "endpoint": "https://overpass-api.de/api/interpreter",
        "bbox": "50.9,6.9,51.3,7.1",
        "region_iso": None,
        "status": "success",
        "tiles": tiles,
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    build()
