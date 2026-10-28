# Quality / error report - Task 098

Every run writes its own machine-generated report to
`reports/quality_report_<run_id>.md` (+ `.json`). This document summarises
what the data looks like, which records are rejected or flagged and why, and
the evidence that the acceptance criteria hold.

## 1. Findings on real data (Dortmund / Bochum / Hagen, NRW)

Source: two unmodified Overpass responses, bbox `51.40,7.30,51.55,7.55`, OSM
snapshot 2026-10-27T18:36Z (`tests/fixtures/real/`). The figures below are
asserted in `tests/test_real_dortmund_sample.py`, so they are reproducible.

| | |
|---|---|
| Substations with a 110/220/380 kV level | 33 (32 ways, 1 multipolygon relation) |
| `voltage` values | `110000` x23, `110000;15000` x3, `380000` x3, `220000;110000` x2, `220000` x2 |
| Substations with own `rating` | **0 of 33** |
| Transformers inside those substations | 56 |
| Transformers with `rating` | 6 - `40 MVA` x3 (parsed), `10 MW` x3 (rejected: active power) |
| Transformers without any voltage tag | 6 |
| Transformers with non-numeric `voltage:secondary` (`medium`) | 3 |
| Not grid candidates | 3 railway traction (16.7 Hz), 2 cable transitions, 1 disused |
| Multi-element sites | Station Kruckel (110 + 380 kV), Garenfeld (220 + 110 kV, same wikidata), Witten (380 + 110 kV) |

What this means for capacity: a substation is documented well enough for a
defensible MVA only when its transformers carry ratings. In this sample the
only rated grid transformers (Station Wambel, 3 x 40 MVA) are on the
110/10 kV level while the station's main 220/110 kV transformers are unrated.
Summing the 120 MVA and calling it "the capacity of Wambel" would be a
fabricated number; the pipeline reports `unknown` for the station and keeps
the 120 MVA in `capacity_by_level`.

Expected NRW-wide outcome: most substations `unknown`
(`no_transformers_mapped` / `transformers_unrated`), a small share
`inferred_*`, very few `observed`. The exact figures come from the per-run report.

## 2. Rejected records (`ingest_rejects`, with raw JSON)

| reason | example |
|---|---|
| `no_target_voltage` | `voltage=11000` - matched the loose Overpass filter, but is 11 kV |
| `coordinates_out_of_range` | latitude 95 |
| `missing_geometry` | node without lat/lon, way without geometry and centre |
| `unexpected_power_tag` | `power=line` returned by the query |
| `malformed_element` | element without `type` / `id` |

## 3. Rejected or downgraded values (row kept, value not used)

| situation | handling | flag / reason |
|---|---|---|
| `rating` in W / kW / MW | not converted | `active_power_unit_not_mva` |
| `rating` with several values (`40;40`) | rejected | `multi_value_ambiguous` |
| `rating` without unit (`40`) | rejected | `missing_unit` |
| `rating` > 5000 MVA or <= 0 | rejected | `implausible_magnitude`, `non_positive` |
| `2x40 MVA`, `40000 kVA` | parsed (80, 40 MVA) | `rating_multiplier_applied`, `rating_converted_from_kva` |
| `rating` on a node with `devices>1` | used as lower bound only | `rating_with_multiple_devices` |
| `voltage=380` (no unit) | read as 380 kV | `voltage_unitless_kv_assumed` |
| `voltage:secondary=medium` | ignored | `voltage_secondary_unparseable` |
| transformer without any voltage | not attributed to a level; prevents `inferred_complete` | `missing_primary_voltage` |

## 4. Geometry edge cases

| situation | handling |
|---|---|
| unclosed way | stored as LineString, `unclosed_way`, cannot hold transformers |
| missing vertex | `partial_geometry` / `incomplete_ring` |
| self-intersecting polygon | stored as is, `geom_valid=false`, `invalid_polygon:Self-intersection`; repaired copy used only for containment and area |
| multipolygon relation | outer rings assembled from open member ways, inner rings subtracted |
| relation that cannot be assembled | falls back to centre point, `relation_assembly_failed` |
| area > 2 km² | `area_implausibly_large` |
| only a centre point available | `center_only`, capacity `unknown / no_polygon_geometry` |
| transformer inside several polygons | smallest polygon wins, `contained_ambiguous` |

## 5. Duplicates

| situation | handling |
|---|---|
| same element in two tiles | kept once (counted as `tile_overlap_duplicates`) |
| substation node inside a substation area with a shared level | `is_duplicate`, `duplicate_of` |
| two areas with IoU >= 0.9 and same levels | the later one `is_duplicate` |
| one site mapped as several areas | not a duplicate: separate rows, shared `site_id` |

## 6. Acceptance evidence

| criterion | evidence |
|---|---|
| No fabricated capacity | every value is re-derived after each run (`verify_capacity_evidence`, report section *Acceptance checks*); SQL CHECK 1-4; tests in `test_pipeline_synthetic.py`, `test_capacity_rules.py` |
| Geometry validation | `test_geometry.py`; SQL CHECK 5-7 (`ST_IsValid` agrees with `geom_valid`, SRID, containment) |
| Unit parsing | `test_units.py` (incl. the real values `40 MVA`, `10 MW`, `220000;110000`, `medium`) |
| Rate limits | `test_overpass_client.py`; the real exploration run hit HTTP 504 once and recovered after the 10 s backoff |
| Deterministic / auditable | raw bytes + sha256 + query per tile in the manifest; byte-identical re-processing test; tamper detection test |
| Staging only, no data loss | schema validator + loader guard; idempotent upsert; vanished rows flagged, never deleted; rejects stored with raw JSON |

## 7. Known gaps

* OSM documents capacity for a small minority of substations.
* Nameplate capacity is not free connection capacity.
* Transformer discovery depends on substation polygons and on Overpass area freshness.
* The public Overpass instance has no SLA; failed tiles are reported and the run is `partial`.
