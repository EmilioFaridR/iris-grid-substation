from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from app.models import (
    MVA_INFERRED_COMPLETE,
    MVA_INFERRED_PARTIAL,
    MVA_OBSERVED,
    MVA_STATUSES,
    MVA_UNKNOWN,
)
from app.parsers.units import RATING_PARSED
from app.runners.process_runner import ProcessResult

# Evidence check


def verify_capacity_evidence(result: ProcessResult) -> list[str]:
    """Return a list of violations (empty list == every MVA value is evidenced)."""
    trafos_by_sub: dict[str, list] = {}
    for t in result.transformers:
        if t.substation_key:
            trafos_by_sub.setdefault(t.substation_key, []).append(t)

    violations: list[str] = []
    for s in result.substations:
        if s.mva_status == MVA_UNKNOWN:
            if s.mva_value is not None:
                violations.append(f"{s.key}: unknown status but value {s.mva_value}")
            continue
        if s.mva_value is None:
            violations.append(f"{s.key}: status {s.mva_status} without value")
            continue
        if s.mva_status == MVA_OBSERVED:
            if s.rating_status != RATING_PARSED or s.rating_mva != s.mva_value:
                violations.append(f"{s.key}: observed value not equal to own rating tag")
            continue
        level = [t for t in trafos_by_sub.get(s.key, []) if t.primary_kv == s.mva_level_kv]
        rated = [t for t in level if t.rating_status == RATING_PARSED]
        expected = sum((t.rating_mva for t in rated), Decimal(0))
        if not rated or expected != s.mva_value:
            violations.append(f"{s.key}: inferred {s.mva_value} != sum of rated transformers {expected}")
        if s.mva_status == MVA_INFERRED_COMPLETE and len(rated) != len(level):
            violations.append(f"{s.key}: marked complete but {len(level) - len(rated)} transformers unrated")
        if s.mva_status == MVA_INFERRED_PARTIAL and not s.mva_is_lower_bound:
            violations.append(f"{s.key}: partial value not marked as lower bound")
    for t in result.transformers:
        if t.rating_status == RATING_PARSED and t.rating_raw and t.rating_raw.strip().lower().endswith("w"):
            violations.append(f"{t.key}: active-power rating {t.rating_raw!r} was used as MVA")
    return violations


# Summary


def _pct(part: int, whole: int) -> str:
    return "n/a" if whole == 0 else f"{100 * part / whole:.1f}%"


def build_summary(result: ProcessResult, load_stats: dict | None = None) -> dict:
    subs = result.substations
    active = [s for s in subs if not s.is_duplicate]
    candidates = [s for s in active if s.is_grid_candidate]
    trafos = result.transformers
    manifest = result.manifest
    tiles = manifest.get("tiles", [])

    flags = Counter(f for s in subs for f in s.quality_flags)
    trafo_flags = Counter(f for t in trafos for f in t.quality_flags)
    level_counts = Counter(kv for s in active for kv in s.hv_levels_kv)

    return {
        "run": {
            "run_id": result.run_id,
            "status": manifest.get("status"),
            "endpoint": manifest.get("endpoint"),
            "bbox": manifest.get("bbox"),
            "region_iso": manifest.get("region_iso"),
            "osm_snapshot_ts": result.osm_snapshot_ts,
            "areas_snapshot_ts": result.areas_snapshot_ts,
            "tiles_total": len(tiles),
            "tiles_ok": sum(t.get("status") == "ok" for t in tiles),
            "tiles_failed": [t["tile_id"] for t in tiles if t.get("status") != "ok"],
            "client_stats": manifest.get("client_stats"),
        },
        "input": dict(result.stats["parse"]),
        "substations": {
            "parsed": len(subs),
            "duplicates_flagged": len(subs) - len(active),
            "unique": len(active),
            "grid_candidates": len(candidates),
            "non_grid_by_reason": dict(Counter(s.non_grid_reason for s in active if not s.is_grid_candidate)),
            "by_target_level_kv": {str(k): level_counts[k] for k in sorted(level_counts, reverse=True)},
            "by_type": dict(Counter(s.substation_type or "(untagged)" for s in active).most_common()),
            "by_geom_kind": dict(Counter(s.geom_kind for s in active)),
            "invalid_polygons": sum(1 for s in active if s.has_polygon and not s.geom_valid),
            "sites": dict(result.stats["sites"]),
            "quality_flags": dict(flags.most_common()),
        },
        "transformers": {
            "parsed": len(trafos),
            "assignment": dict(result.stats["assignment"]),
            "with_primary_voltage": sum(t.primary_v is not None for t in trafos),
            "rating_status": dict(Counter(t.rating_status for t in trafos)),
            "rating_rejected_by_reason": dict(Counter(
                t.rating_reject_reason for t in trafos if t.rating_reject_reason)),
            "quality_flags": dict(trafo_flags.most_common()),
        },
        "capacity_grid_candidates": {
            "by_status": {st: sum(s.mva_status == st for s in candidates) for st in MVA_STATUSES},
            "with_any_mva": sum(s.mva_value is not None for s in candidates),
            "coverage_pct": _pct(sum(s.mva_value is not None for s in candidates), len(candidates)),
            "unknown_by_reason": dict(Counter(
                s.mva_unknown_reason for s in candidates if s.mva_status == MVA_UNKNOWN).most_common()),
            "rated_only_at_lower_level": sum(
                "rated_capacity_only_at_lower_level" in s.quality_flags for s in candidates),
        },
        "rejects_by_reason": dict(Counter(f"{r.element_kind}:{r.reason}" for r in result.rejects).most_common()),
        "evidence_violations": verify_capacity_evidence(result),
        "load": load_stats,
    }


# Markdown


def _table(rows: list[tuple], header: tuple) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _dict_table(data: dict, header=("value", "count")) -> str:
    if not data:
        return "_none_"
    return _table([(k, v) for k, v in data.items()], header)


def render_markdown(result: ProcessResult, summary: dict) -> str:
    run, subs, tr, cap = summary["run"], summary["substations"], summary["transformers"], summary["capacity_grid_candidates"]
    violations = summary["evidence_violations"]
    with_mva = sorted(
        (s for s in result.substations if s.mva_value is not None and not s.is_duplicate),
        key=lambda s: (-s.mva_value, s.key),
    )

    lines = [
        f"# Quality report - run `{run['run_id']}`",
        "",
        f"- **Status:** {run['status']} ({run['tiles_ok']}/{run['tiles_total']} tiles ok"
        + (f", failed: {', '.join(run['tiles_failed'])}" if run["tiles_failed"] else "") + ")",
        f"- **OSM snapshot:** {run['osm_snapshot_ts']} (areas: {run['areas_snapshot_ts']})",
        f"- **Scope:** bbox `{run['bbox']}`, region `{run['region_iso']}`",
        f"- **Source:** {run['endpoint']} - (c) OpenStreetMap contributors, ODbL 1.0",
    ]
    if run.get("client_stats"):
        cs = run["client_stats"]
        lines.append(f"- **Requests:** {cs['requests']} ({cs['retries']} retries, "
                     f"{cs['waited_s']} s waiting; failures {cs['failures_by_reason'] or 'none'})")

    lines += [
        "",
        "## Acceptance checks",
        "",
        _table([
            ("No fabricated capacity (every MVA value re-derived from its evidence)",
             "PASS" if not violations else f"FAIL ({len(violations)})"),
            ("Active-power ratings (MW) never used as MVA",
             "PASS" if not any("active-power" in v for v in violations) else "FAIL"),
            ("Partial sums flagged as lower bound",
             "PASS" if not any("lower bound" in v for v in violations) else "FAIL"),
            ("Invalid polygons detected and flagged (not silently dropped)", f"{subs['invalid_polygons']} flagged"),
            ("Rejected elements stored with reason", f"{sum(summary['rejects_by_reason'].values())} rejects"),
        ], ("check", "result")),
    ]
    if violations:
        lines += ["", "Violations:", ""] + [f"- {v}" for v in violations]

    lines += [
        "",
        "## Capacity coverage (unique grid-candidate substations)",
        "",
        f"**{cap['with_any_mva']} of {subs['grid_candidates']} ({cap['coverage_pct']})** have a defensible MVA value.",
        "",
        _dict_table(cap["by_status"], ("mva_status", "substations")),
        "",
        "Why the rest is `unknown`:",
        "",
        _dict_table(cap["unknown_by_reason"], ("mva_unknown_reason", "substations")),
        "",
        f"Substations whose only rated transformers are on a lower transformation level "
        f"(reported in `capacity_by_level`, not in `mva_value`): {cap['rated_only_at_lower_level']}",
    ]
    if with_mva:
        lines += ["", "Substations with a value:", "",
                  _table([(s.key, s.name or "", s.mva_status, s.mva_value,
                           "yes" if s.mva_is_lower_bound else "no", s.mva_level_kv)
                          for s in with_mva[:50]],
                         ("element", "name", "status", "MVA", "lower bound", "level kV"))]

    lines += [
        "",
        "## Substations",
        "",
        f"- Parsed: {subs['parsed']} - duplicates flagged: {subs['duplicates_flagged']} - unique: {subs['unique']}",
        f"- Grid candidates: {subs['grid_candidates']} (excluded: {subs['non_grid_by_reason'] or 'none'})",
        f"- Sites: {subs['sites'].get('sites', 0)} "
        f"({subs['sites'].get('multi_element_sites', 0)} sites made of several elements)",
        f"- Invalid polygons: {subs['invalid_polygons']}",
        "",
        "By target voltage level (a substation can have several):",
        "",
        _dict_table(subs["by_target_level_kv"], ("kV", "substations")),
        "",
        "By `substation=` type:",
        "",
        _dict_table(subs["by_type"], ("type", "substations")),
        "",
        "By geometry kind:",
        "",
        _dict_table(subs["by_geom_kind"], ("geom_kind", "substations")),
        "",
        "Quality flags:",
        "",
        _dict_table(subs["quality_flags"], ("flag", "substations")),
        "",
        "## Transformers",
        "",
        f"- Parsed: {tr['parsed']} - with primary voltage: {tr['with_primary_voltage']}",
        "",
        _dict_table(tr["assignment"], ("assignment", "transformers")),
        "",
        _dict_table(tr["rating_status"], ("rating_status", "transformers")),
        "",
        "Rejected ratings:",
        "",
        _dict_table(tr["rating_rejected_by_reason"], ("reason", "transformers")),
        "",
        "Quality flags:",
        "",
        _dict_table(tr["quality_flags"], ("flag", "transformers")),
        "",
        "## Rejected elements",
        "",
        _dict_table(summary["rejects_by_reason"], ("kind:reason", "elements")),
        "",
        "## Input",
        "",
        _dict_table(summary["input"], ("metric", "count")),
    ]
    if summary.get("load"):
        lines += ["", "## Load into iris_staging", "", "```json",
                  json.dumps(summary["load"], indent=2), "```"]
    lines += [
        "",
        "## Known gaps",
        "",
        "- OSM is volunteered data: most substations and transformers carry no `rating`. "
        "`unknown` means *not documented in OSM*, not *no capacity*.",
        "- Transformers are only found inside substation **areas**; substations mapped as a "
        "single node (or only a centre point) cannot have transformers assigned.",
        "- Transformer pre-filtering relies on Overpass areas, which lag the OSM data "
        f"(areas snapshot: {run['areas_snapshot_ts']}); substations drawn after that may show no transformers.",
        "- Nameplate transformer capacity is an upper bound of what the grid can deliver; it says "
        "nothing about *free* capacity (N-1 reserve, existing load). That needs operator data.",
        "",
    ]
    return "\n".join(lines)


def write_report(result: ProcessResult, report_dir: Path, load_stats: dict | None = None) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(result, load_stats)
    md_path = report_dir / f"quality_report_{result.run_id}.md"
    json_path = report_dir / f"quality_report_{result.run_id}.json"
    md_path.write_text(render_markdown(result, summary), encoding="utf-8")
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return md_path, json_path
