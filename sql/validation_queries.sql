

-- OVERVIEW

-- Coverage by mva_status for unique grid-candidate substations
SELECT mva_status, count(*) AS substations, round(sum(mva_value), 1) AS mva_total
FROM iris_staging.substations_mva
WHERE NOT is_duplicate AND is_grid_candidate AND present_in_latest_snapshot
GROUP BY mva_status
ORDER BY substations DESC;

-- Why capacity is unknown
SELECT mva_unknown_reason, count(*)
FROM iris_staging.substations_mva
WHERE mva_status = 'unknown' AND NOT is_duplicate AND is_grid_candidate
GROUP BY 1 ORDER BY 2 DESC;

-- One row per physical site (several voltage-level areas collapse into one)
SELECT site_id,
       array_agg(osm_type || '/' || osm_id ORDER BY max_voltage_kv DESC) AS elements,
       max(max_voltage_kv) AS max_kv,
       string_agg(DISTINCT name, ' / ') AS names
FROM iris_staging.substations_mva
WHERE NOT is_duplicate
GROUP BY site_id
HAVING count(*) > 1
ORDER BY max_kv DESC;

-- Last runs
SELECT run_id, status, osm_snapshot_ts, tiles_ok, tiles_failed,
       stats -> 'load' -> 'substations' AS substations_load
FROM iris_staging.ingest_runs
ORDER BY loaded_at DESC
LIMIT 5;

-- CHECKS 

-- CHECK 1 - no fabricated capacity: every inferred value equals the sum of the
-- parsed ratings of the transformers assigned to it at mva_level_kv.
WITH evidence AS (
    SELECT t.substation_osm_type AS osm_type, t.substation_osm_id AS osm_id,
           t.primary_v / 1000 AS level_kv, sum(t.rating_mva) AS rated_sum
    FROM iris_staging.substation_transformers t
    WHERE t.rating_status = 'parsed' AND t.substation_osm_id IS NOT NULL
    GROUP BY 1, 2, 3
)
SELECT s.osm_type, s.osm_id, s.mva_status, s.mva_value, e.rated_sum
FROM iris_staging.substations_mva s
LEFT JOIN evidence e
       ON e.osm_type = s.osm_type AND e.osm_id = s.osm_id AND e.level_kv = s.mva_level_kv
WHERE s.mva_status IN ('inferred_complete', 'inferred_partial')
  AND (e.rated_sum IS NULL OR e.rated_sum <> s.mva_value);

-- CHECK 2 - observed values come from the substation's own rating tag
SELECT osm_type, osm_id, rating_raw, mva_value
FROM iris_staging.substations_mva
WHERE mva_status = 'observed' AND (rating_status <> 'parsed' OR mva_value IS NULL);

-- CHECK 3 - unknown means NULL, partial means lower bound
SELECT osm_type, osm_id, mva_status, mva_value, mva_is_lower_bound
FROM iris_staging.substations_mva
WHERE (mva_status = 'unknown' AND mva_value IS NOT NULL)
   OR (mva_status = 'inferred_partial' AND NOT mva_is_lower_bound)
   OR (mva_status <> 'inferred_partial' AND mva_is_lower_bound);

-- CHECK 4 - active-power ratings (W, kW, MW) are never accepted as MVA
SELECT osm_type, osm_id, rating_raw
FROM iris_staging.substation_transformers
WHERE rating_status = 'parsed' AND rating_raw ~* '[0-9]\s*[gmk]?w\s*$';

-- CHECK 5 - geom_valid agrees with PostGIS
SELECT osm_type, osm_id, geom_kind, geom_valid, ST_IsValidReason(geom)
FROM iris_staging.substations_mva
WHERE geom_kind IN ('polygon', 'multipolygon') AND geom_valid <> ST_IsValid(geom);

-- CHECK 6 - SRID and coordinate range
SELECT osm_type, osm_id
FROM iris_staging.substations_mva
WHERE ST_SRID(geom) <> 4326
   OR ST_Y(centroid) NOT BETWEEN -90 AND 90
   OR ST_X(centroid) NOT BETWEEN -180 AND 180;

-- CHECK 7 - every assigned transformer lies inside its substation
SELECT t.osm_type, t.osm_id, t.substation_osm_type, t.substation_osm_id
FROM iris_staging.substation_transformers t
JOIN iris_staging.substations_mva s
  ON s.osm_type = t.substation_osm_type AND s.osm_id = t.substation_osm_id
WHERE NOT ST_Intersects(ST_MakeValid(s.geom), t.geom);

-- CHECK 8 - duplicates point at an existing substation
SELECT d.osm_type, d.osm_id, d.duplicate_of
FROM iris_staging.substations_mva d
LEFT JOIN iris_staging.substations_mva s ON (s.osm_type || '/' || s.osm_id) = d.duplicate_of
WHERE d.is_duplicate AND s.osm_id IS NULL;

-- CHECK 9 - only target voltage levels in scope
SELECT osm_type, osm_id, hv_levels_kv
FROM iris_staging.substations_mva
WHERE NOT (hv_levels_kv && ARRAY[110, 220, 380]);
