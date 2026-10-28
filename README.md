# IRIS-GRID-SUBSTATION · Task 098

Geospatial inventory of **110 / 220 / 380 kV substations** in North
Rhine-Westphalia from OpenStreetMap (the data behind OpenInfraMap), with a
transformer capacity in **MVA that is either observed, inferred from evidence,
or explicitly unknown - never fabricated**.

Destination: `iris_staging.substations_mva` (plus `substation_transformers`,
`ingest_rejects`, `ingest_runs`). Nothing is ever written to `iris_core`.

---

## Quick start

### Docker (recommended)

```bash
cp .env.example .env                      # set a contact e-mail in IRIS_USER_AGENT
docker compose up --build app             # fetch NRW -> process -> load into local PostGIS
docker compose run --rm tests             # 127 tests incl. PostGIS integration tests
```

Results:

| Output | Where |
|---|---|
| Raw Overpass responses + manifest (query, sha256, OSM snapshot) | `data/raw/overpass/<run_id>/` |
| `substations_mva.csv`, `.geojson`, `substation_transformers.csv`, `ingest_rejects.csv` | `data/processed/<run_id>/` |
| Quality report (Markdown + JSON) | `reports/quality_report_<run_id>.md` |
| Staging tables | PostGIS in the compose network, schema `iris_staging` (to reach it from the host: `docker compose -f docker-compose.yml -f docker-compose.db-port.yml up -d db` -> `localhost:55433`) |

Re-process a stored run without touching the network:
`docker compose run --rm app process --run-id <run_id>`.

### Without Docker

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install ".[dev]"
python -m pytest                                  # integration tests skip without IRIS_TEST_DATABASE_URL
python main.py process --raw-file tests/fixtures/real/dortmund_substations_center.json tests/fixtures/real/dortmund_transformers_center.json   # offline, real sample
python main.py fetch --bbox 51.40,7.30,51.55,7.55 --region ""  # small live area
python main.py process                                          # latest fetched run
```

`load` / `all` additionally need `IRIS_DATABASE_URL` (see `.env.example`).

---

## Pipeline

The structure mirrors the IRIS core repo (`app/runners`, `app/scrapers`,
`app/parsers`, `app/transformers`, `app/loaders`, `main.py` + `PipelineRunner`).

```
fetch     app/scrapers/overpass   tile the bbox, query Overpass politely, store raw bytes + manifest
process   app/parsers             elements -> records; unit parsing; geometry build + validation
          app/transformers        dedup -> transformer assignment -> capacity -> site grouping
          app/exporters           CSV / GeoJSON
          app/reports             quality report incl. re-derivation of every MVA value
load      app/loaders             idempotent upsert into iris_staging (one transaction per run)
```

`process` is a pure function of the raw files: the same `data/raw/overpass/<run_id>/`
always yields byte-identical outputs (tested), and a modified raw file is
detected through its sha256.

> Naming: as in the core repo, `app/transformers` are *data* transformers.
> Electrical transformers are `TransformerRecord` / `substation_transformers`.

---

## Data provenance

| | |
|---|---|
| Source | OpenStreetMap, via the public Overpass API `overpass-api.de` |
| Licence | ODbL 1.0 - "(c) OpenStreetMap contributors" (kept in the GeoJSON and manifest) |
| Scope | bbox `50.32,5.86,52.53,9.46` (as in the IRIS core pipeline) clipped to `ISO3166-2=DE-NW`, 12 tiles of 1° |
| Freshness | OSM is edited continuously; Overpass applies minutely diffs (`timestamp_osm_base`, stored per run). Overpass *areas* are rebuilt less often (`timestamp_areas_base`, also stored). A weekly run is sufficient. |
| Reproducibility | exact query text, endpoint, attempts, byte size and sha256 of every tile in `manifest.json` |

Query per tile (`app/scrapers/overpass/queries.py`):

```
[out:json][timeout:180];
area["ISO3166-2"="DE-NW"]["boundary"="administrative"]->.region;
nwr["power"="substation"]["voltage"~"110|220|380"](area.region)(S,W,N,E)->.subs;
.subs out geom;                                  // full polygons, needed for containment
.subs map_to_area->.subareas;
nwr["power"="transformer"](area.subareas)->.trafos;
.trafos out center;
```

The voltage filter is deliberately loose (it also matches `110 kV` or
`11000`); the strict decision is made in Python so mis-tagged values show up
in the report instead of disappearing.

### Lawful, polite access

* identifiable `User-Agent` (configure your contact in `.env`);
* only whitelisted public endpoints (`IRIS_ALLOWED_OVERPASS_HOSTS`), checked at start-up;
* `/api/status` is read before each query and the client waits for a free slot;
* minimum interval between requests (default 5 s);
* retries with exponential backoff and `Retry-After` for 429 / 502 / 503 / 504,
  network errors and **runtime errors inside an HTTP 200** (Overpass returns
  partial data plus a `remark` when a query times out - accepting it would
  silently lose substations);
* HTTP 400 (bad query) is not retried;
* a failed tile does not abort the run; the run is stored as `partial`.

---

## Schema mapping

### `iris_staging.substations_mva` - one row per OSM element

| Column | Source / rule |
|---|---|
| `osm_type`, `osm_id` (PK) | OSM identity - stable id across runs |
| `name`, `operators[]`, `operator_wikidata`, `wikidata` | `name`, `operator` (split on `;`), `operator:wikidata`, `wikidata` |
| `substation_type`, `lifecycle`, `frequency_hz` | `substation=`, `disused`/`abandoned`, `frequency` |
| `voltage_raw`, `voltages_v[]`, `hv_levels_kv[]`, `max_voltage_kv` | `voltage` parsed (`220000;110000` -> `{220000,110000}`, `{220,110}`) |
| `rating_raw`, `rating_status`, `rating_reject_reason` | the substation's own `rating` tag |
| `mva_status`, `mva_value`, `mva_level_kv`, `mva_is_lower_bound`, `mva_unknown_reason` | see *Capacity semantics* |
| `capacity_by_level` (jsonb) | per transformation level: counts, rated MVA sum, completeness, transformer ids |
| `transformer_count`, `transformer_rated_count` | transformers assigned by containment |
| `geom` (EPSG:4326), `centroid`, `geom_kind`, `geom_valid`, `area_m2` | `out geom`; validity per GEOS/PostGIS; geodesic area |
| `is_grid_candidate`, `non_grid_reason` | false for `traction`, `transition`, disused/abandoned |
| `is_duplicate`, `duplicate_of`, `site_id`, `site_group_reason` | see *Duplicates and sites* |
| `quality_flags[]`, `tags` (jsonb) | every issue found + all raw tags |
| `content_hash`, `first_seen_run_id`, `last_seen_run_id`, `present_in_latest_snapshot`, `osm_snapshot_ts`, `updated_at` | change tracking |

`substation_transformers`: one row per `power=transformer`, with its parsed
`voltage:primary/secondary/tertiary`, rating and the substation it was
assigned to. `ingest_rejects`: every element that could not become a row,
with reason and raw JSON. `ingest_runs`: one row per run with snapshot
timestamps, tile status and statistics.

---

## Capacity semantics

OSM rarely documents capacity (in the real Dortmund sample: **0 of 33**
substations and **6 of 56** transformers carry a `rating`, and 3 of those are
in MW). The pipeline therefore reports what can be proven and says why the
rest is unknown.

| `mva_status` | Meaning | `mva_value` |
|---|---|---|
| `observed` | the substation has its own parseable `rating` in VA units | that rating |
| `inferred_complete` | every transformer of the **top transformation level** (primary voltage = highest voltage of the substation) is rated | sum of their ratings |
| `inferred_partial` | only some are rated, or some transformers have no voltage, or a rating may cover several devices | sum of the rated ones, `mva_is_lower_bound = true` |
| `unknown` | nothing defensible | `NULL` + `mva_unknown_reason` |

Rules that prevent fabrication:

* **No unit conversion across power types.** `10 MW` is active power; MVA is
  apparent power. Converting needs a power factor OSM does not have, so the
  rating is rejected (`active_power_unit_not_mva`). VA units are converted
  (`40000 kVA` -> 40 MVA).
* **Ambiguous values are rejected, not interpreted**: `40;40` (per device? per
  winding?), missing unit, unparseable text, implausible magnitude.
* **No summing across levels.** A 380/110 kV and a 110/10 kV transformer are
  in series; adding them double counts. Lower levels stay in
  `capacity_by_level`. Example from the real data, *Station Wambel*: three
  110/10 kV transformers of 40 MVA are rated, its two 220/110 kV transformers
  are not -> `mva_status = unknown`, `capacity_by_level` shows 120 MVA at
  110/10 kV, flag `rated_capacity_only_at_lower_level`.
* **No estimates by voltage class**, no defaults, no nearest-neighbour guesses.
* Observed and inferred values are compared; differences are flagged
  (`observed_differs_from_transformer_sum`).
* `verify_capacity_evidence()` re-derives every non-null value from the
  transformer rows after each run; the result is the first line of the
  quality report, and the same check exists as SQL (`sql/validation_queries.sql`).

Nameplate capacity is not *free* capacity; see Limitations.

---

## Duplicates and sites

* **Tile overlap**: the same element returned by two tiles is kept once (by OSM id).
* **Node inside area**: a `power=substation` node inside a substation area that
  shares a voltage level -> `is_duplicate`, `duplicate_of = way/...`.
* **Area drawn twice**: overlapping areas with IoU >= 0.9 and identical levels.
* Duplicates are flagged, loaded and excluded from metrics - never deleted.
* **Sites**: large stations are often one area per voltage level/operator
  (real example: *Station Kruckel* 110 kV and 380 kV, ~100 m apart). Elements
  are grouped into a `site_id` when they share `wikidata`, share a normalised
  name/alt_name within 500 m, or are within 30 m of each other. Railway
  traction substations are never grouped with public-grid ones (real example:
  *Unterwerk Dortmund* vs *Dorstfeld*, ~150 m apart).

Transformers are assigned with a point-in-polygon test (PostGIS-equivalent
predicates in shapely). Invalid polygons are repaired with `make_valid` for
that test only; the stored geometry is the original and `geom_valid = false`.

---

## Staging safety and idempotency

* `IRIS_STAGING_SCHEMA` must start with `iris_staging`; `iris_core` / `public`
  are refused by the settings validator **and** again inside the loader.
* Alembic keeps its version table inside the staging schema.
* Upsert on `(osm_type, osm_id)`; a content hash tells
  inserted / updated / unchanged rows apart; `first_seen_run_id` is preserved.
* One transaction per run; a failed load is recorded in `ingest_runs` as `failed`.
* Nothing is deleted. After a **complete** run, rows of the same bbox not seen
  any more get `present_in_latest_snapshot = false`; partial runs never do this.

---

## Tests and acceptance evidence

`python -m pytest` - 127 tests (unit tests on real and synthetic data,
mocked HTTP; integration tests run when `IRIS_TEST_DATABASE_URL` points at PostGIS).

| Acceptance criterion | Evidence |
|---|---|
| No fabricated capacity | `test_pipeline_synthetic.py::test_no_fabricated_capacity`, `test_capacity_rules.py`, SQL CHECK 1-4, report section *Acceptance checks* |
| Geometry-validation tests | `test_geometry.py` (unclosed ways, bow-tie polygons, missing vertices, out-of-range coordinates, multipolygon assembly), SQL CHECK 5-7 |
| Unit-parsing tests | `test_units.py` (kV/V/unitless, kVA/MVA/GVA, `2x40`, German decimal comma, MW rejected) |
| Rate-limit tests | `test_overpass_client.py` (429 + Retry-After, 504 backoff and cap, runtime error inside 200, min interval, `/api/status` slots, no retry on 400) |
| Missing / malformed / duplicate records | `test_pipeline_synthetic.py::test_rejects_are_kept_with_reason`, `test_duplicates`, `test_real_dortmund_sample.py` |
| Deterministic / auditable | `test_processing_is_deterministic`, `test_tampered_raw_file_is_detected`, manifest + sha256 |
| Staging-only, repeatable, no data loss | `test_loader_integration.py` (idempotent reload, changed and vanished rows, partial runs, iris_core refused) |
| Documented source | fixtures from the real API in `tests/fixtures/real/` (see its README) |

---

## Assumptions

* Scope is NRW (`DE-NW`), as in the IRIS core pipeline; any bbox/region can be configured.
* Target levels are 110, 220 and 380 kV; other levels of the same substation are stored but not targeted.
* OSM `voltage` is in volts; a bare `110`, `220` or `380` is read as kV and flagged `voltage_unitless_kv_assumed`.
* A substation's capacity is the rated power of its top transformation level.
* `generation` and `industrial` substations stay grid candidates (they connect to the
  public HV grid - e.g. power-plant sites are relevant for data centres); only
  `traction`, `transition` and disused/abandoned ones are excluded. Configurable.
* Distances use ETRS89 / UTM 32N (EPSG:25832); areas are geodesic (WGS84).

## Limitations

* Capacity coverage is bounded by OSM documentation; `unknown` means *not
  documented*, not *no capacity*.
* Nameplate transformer power is an upper bound of deliverable power. Free
  connection capacity (N-1 reserve, existing load, grid-operator queue) needs
  operator data (e.g. Amprion/Westnetz grid-connection maps), which OSM does not contain.
* Transformers are found only inside substation **areas**; substations mapped as a node get none.
* Transformer pre-filtering uses Overpass areas, which lag OSM edits (see `timestamp_areas_base`).
* Site grouping is rule-based; borderline cases are visible through `site_group_reason`.
* The public Overpass instance has no SLA; large regions should keep the tile size at 1° or less.

---

## Configuration

All settings are environment variables with prefix `IRIS_` (`app/config.py`,
`.env.example`). The most relevant:

| Variable | Default | |
|---|---|---|
| `IRIS_DATABASE_URL` | - | required for `load` / `all` |
| `IRIS_STAGING_SCHEMA` | `iris_staging` | must start with `iris_staging` |
| `IRIS_BBOX` | `50.32,5.86,52.53,9.46` | south,west,north,east |
| `IRIS_REGION_ISO` | `DE-NW` | `""` = bbox only |
| `IRIS_TILE_DEG` | `1.0` | tile size |
| `IRIS_MIN_REQUEST_INTERVAL_S` | `5` | politeness delay |
| `IRIS_MAX_RETRIES` / `IRIS_BACKOFF_BASE_S` | `4` / `10` | retry policy |
| `IRIS_NON_GRID_SUBSTATION_TYPES` | `traction,transition` | excluded from grid candidates |
| `IRIS_SITE_NAME_MAX_DISTANCE_M` / `IRIS_SITE_ADJACENCY_MAX_DISTANCE_M` | `500` / `30` | site grouping |
| `IRIS_LOG_JSON` | `false` | JSON log lines with `source`, `run_id` |

## Project layout

```
app/
  config.py              settings + safety validators
  models.py              records and MVA status constants
  scrapers/overpass/     client (rate limit, retries), queries/tiles, fetch stage
  parsers/               units, geometry, element parser
  transformers/          dedup, assignment, capacity, sites
  loaders/               staging upsert
  database/              table definitions, engine
  exporters/, reports/   CSV/GeoJSON, quality report
  runners/               process stage, CLI (PipelineRunner)
alembic/                 migration 0001 (schema iris_staging)
sql/validation_queries.sql
tests/                   unit + integration tests, real and synthetic fixtures
QUALITY_REPORT.md        findings, rejected data, edge cases
```
